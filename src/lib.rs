use ::maxminddb as maxminddb_crate;
use arc_swap::ArcSwapOption;
use maxminddb_crate::{MaxMindDbError, PathElement, Reader as MaxMindReader};
use memmap2::Mmap;
use pyo3::{
    exceptions::{PyAttributeError, PyOSError, PyRuntimeError, PyTypeError, PyValueError},
    prelude::*,
    types::{
        PyByteArray, PyBytes, PyList, PyListMethods, PyModule, PyString, PyTuple, PyTupleMethods,
    },
};
use std::{cell::RefCell, ffi::OsString, net::IpAddr, path::PathBuf, sync::Arc};

thread_local! {
    static PATH_CACHE: RefCell<PathCache> = const { RefCell::new(PathCache::new()) };
}

mod decode;
mod ip;
mod iterator;
mod metadata;
mod open;
mod path;

use decode::PyDecodedValue;
use ip::{ipv6_in_ipv4_error, parse_ip_address};
use iterator::ReaderIterator;
use metadata::Metadata;
use open::{open_database_fd, open_database_memory, open_database_mmap};
use path::{parse_path, path_elements_from_owned_path, path_tuple_matches_owned, OwnedPathElement};

// Define InvalidDatabaseError exception (subclass of RuntimeError)
pyo3::create_exception!(
    maxminddb_rust,
    InvalidDatabaseError,
    PyRuntimeError,
    "Invalid MaxMind DB"
);

// Mode constants matching original maxminddb module
const MODE_AUTO: i32 = 0;
const MODE_MMAP_EXT: i32 = 1;
const MODE_MMAP: i32 = 2;
const MODE_FILE: i32 = 4;
const MODE_MEMORY: i32 = 8;
const MODE_FD: i32 = 16;

// Error message constants
const ERR_CLOSED_DB: &str = "Attempt to read from a closed MaxMind DB.";
const ERR_BAD_DATA: &str =
    "The MaxMind DB file's data section contains bad data (unknown data type or corrupt data)";
const ERR_BAD_DATABASE_ARG: &str = "database must be a string, PathLike, or file descriptor";
const ERR_IPS_ITERABLE: &str = "ips must be an iterable of strings or ipaddress objects";

#[derive(Copy, Clone, Debug, Eq, PartialEq)]
enum OpenMode {
    MmapExt,
    Mmap,
    File,
    Memory,
    Fd,
}

impl TryFrom<i32> for OpenMode {
    type Error = PyErr;

    fn try_from(mode: i32) -> Result<Self, Self::Error> {
        match mode {
            MODE_AUTO => Ok(Self::Mmap),
            MODE_MMAP_EXT => Ok(Self::MmapExt),
            MODE_MMAP => Ok(Self::Mmap),
            MODE_FILE => Ok(Self::File),
            MODE_MEMORY => Ok(Self::Memory),
            MODE_FD => Ok(Self::Fd),
            _ => Err(PyValueError::new_err(format!(
                "Unsupported open mode ({mode})"
            ))),
        }
    }
}

type CachedPath = (Py<PyTuple>, Arc<Vec<OwnedPathElement>>);

const PATH_CACHE_SLOTS: usize = 32;

/// Per-thread cache of parsed tuple paths. A parsed path does not depend on
/// the reader, so one cache serves every Reader on the thread without a lock.
struct PathCache {
    entries: [Option<CachedPath>; PATH_CACHE_SLOTS],
    next: usize,
}

impl PathCache {
    const fn new() -> Self {
        Self {
            entries: [const { None }; PATH_CACHE_SLOTS],
            next: 0,
        }
    }

    /// Find a cached path by tuple identity first, then by tuple contents.
    fn find(&self, path: &Bound<'_, PyTuple>) -> Option<Arc<Vec<OwnedPathElement>>> {
        let path_ptr = path.as_ptr();
        self.entries
            .iter()
            .flatten()
            .find(|(cached_tuple, _)| cached_tuple.as_ptr() == path_ptr)
            .or_else(|| {
                self.entries
                    .iter()
                    .flatten()
                    .find(|(_, cached_path)| path_tuple_matches_owned(path, cached_path))
            })
            .map(|(_, cached_path)| Arc::clone(cached_path))
    }

    /// Insert an entry and return the evicted one. The caller must drop the
    /// evicted entry after releasing the borrow, because releasing the tuple can
    /// run Python code that looks up a path.
    fn insert(&mut self, entry: CachedPath) -> Option<CachedPath> {
        let evicted = self.entries[self.next].replace(entry);
        self.next = (self.next + 1) % PATH_CACHE_SLOTS;
        evicted
    }
}

/// Enum to handle different reader source types
pub(crate) enum ReaderSource {
    Mmap(MaxMindReader<Mmap>),
    Memory(MaxMindReader<Vec<u8>>),
}

impl ReaderSource {
    #[inline]
    fn lookup(
        &self,
        ip: IpAddr,
    ) -> Result<Option<PyDecodedValue>, maxminddb_crate::MaxMindDbError> {
        match self {
            ReaderSource::Mmap(reader) => {
                let result = reader.lookup(ip)?;
                result.decode()
            }
            ReaderSource::Memory(reader) => {
                let result = reader.lookup(ip)?;
                result.decode()
            }
        }
    }

    #[inline]
    fn lookup_prefix(
        &self,
        ip: IpAddr,
    ) -> Result<(Option<PyDecodedValue>, usize), maxminddb_crate::MaxMindDbError> {
        match self {
            ReaderSource::Mmap(reader) => {
                let result = reader.lookup(ip)?;
                let network = result.network()?;
                let prefix_len = convert_prefix_len(ip, network);
                let data = result.decode()?;
                Ok((data, prefix_len))
            }
            ReaderSource::Memory(reader) => {
                let result = reader.lookup(ip)?;
                let network = result.network()?;
                let prefix_len = convert_prefix_len(ip, network);
                let data = result.decode()?;
                Ok((data, prefix_len))
            }
        }
    }

    #[inline]
    fn lookup_path(
        &self,
        ip: IpAddr,
        path_elements: &[PathElement<'_>],
    ) -> Result<Option<PyDecodedValue>, maxminddb_crate::MaxMindDbError> {
        match self {
            ReaderSource::Mmap(reader) => {
                let result = reader.lookup(ip)?;

                result.decode_path(path_elements)
            }

            ReaderSource::Memory(reader) => {
                let result = reader.lookup(ip)?;

                result.decode_path(path_elements)
            }
        }
    }

    #[inline]
    fn metadata(&self) -> &maxminddb_crate::Metadata {
        match self {
            ReaderSource::Mmap(reader) => reader.metadata(),
            ReaderSource::Memory(reader) => reader.metadata(),
        }
    }
}

/// Convert prefix length from database network to the perspective of the queried IP.
/// When an IPv4 address is looked up in an IPv6 database, the network is returned
/// in IPv6 terms. We need to convert it back to IPv4 terms for the user.
#[inline]
fn convert_prefix_len(queried_ip: IpAddr, network: ipnetwork::IpNetwork) -> usize {
    let prefix = network.prefix() as usize;
    match (queried_ip, network) {
        // IPv4 query, IPv6 result: convert from IPv6 prefix to IPv4 prefix
        // IPv4-mapped IPv6 addresses have a 96-bit prefix (::ffff:0:0/96)
        // So the IPv4-relevant part starts at bit 96
        (IpAddr::V4(_), ipnetwork::IpNetwork::V6(_)) => prefix.saturating_sub(96),
        // Same address family: use prefix as-is
        _ => prefix,
    }
}

/// A Python wrapper around the MaxMind DB reader.
/// Supports memory-mapped files (MODE_MMAP) and read-file modes (MODE_FILE/MODE_MEMORY/MODE_FD).
#[pyclass(module = "maxminddb_rust")]
struct Reader {
    reader: ArcSwapOption<ReaderSource>,
    ip_version: u16,
}

#[pymethods]
impl Reader {
    #[new]
    #[pyo3(signature = (database, mode=MODE_AUTO))]
    fn new(database: &Bound<'_, PyAny>, mode: i32) -> PyResult<Self> {
        let open_mode = Self::resolve_open_mode(mode)?;
        Self::open_reader_from_mode(database, open_mode)
    }

    /// Check if the database has been closed.
    ///
    /// Returns:
    ///     True if the database has been closed, False otherwise.
    ///
    /// Example:
    ///     >>> reader = maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb')
    ///     >>> reader.closed
    ///     False
    ///     >>> reader.close()
    ///     >>> reader.closed
    ///     True
    #[getter]
    fn closed(&self) -> bool {
        self.reader.load().is_none()
    }

    /// Query the database for information about an IP address.
    ///
    /// Args:
    ///     ip_address: The IP address to look up. May be a string (e.g., '1.2.3.4')
    ///         or an ipaddress.IPv4Address or ipaddress.IPv6Address object.
    ///
    /// Returns:
    ///     The database record value for the IP address, or None if the address
    ///     is not in the database.
    ///
    /// Raises:
    ///     ValueError: If the database has been closed or the IP address is invalid.
    ///     InvalidDatabaseError: If the database data is corrupt or invalid.
    ///
    /// Example:
    ///     >>> reader = maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb')
    ///     >>> reader.get('8.8.8.8')
    ///     {'city': {'names': {'en': 'Mountain View'}}, ...}
    #[inline]
    fn get(&self, py: Python, ip_address: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        let parsed_ip = self.parse_lookup_ip(ip_address)?;

        let reader = self.reader.load();
        let reader = reader
            .as_ref()
            .ok_or_else(|| PyValueError::new_err(ERR_CLOSED_DB))?;

        self.lookup_result_to_python(py, reader.lookup(parsed_ip))
    }

    /// Query the database for a specific path within the record.
    ///
    /// This method is more efficient than get() when you only need a specific field
    /// (e.g., country code) from the record, as it avoids decoding the entire record.
    ///
    /// Args:
    ///     ip_address: The IP address to look up. May be a string (e.g., '1.2.3.4')
    ///         or an ipaddress.IPv4Address or ipaddress.IPv6Address object.
    ///     path: A sequence (tuple or list) of strings or integers representing the
    ///         path to the data.
    ///
    /// Returns:
    ///     The value at the specified path, or None if the IP address or path is
    ///     not found.
    ///
    /// Example:
    ///     >>> reader.get_path('8.8.8.8', ('country', 'iso_code'))
    ///     'US'
    fn get_path(
        &self,
        py: Python,
        ip_address: &Bound<'_, PyAny>,
        path: &Bound<'_, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        let parsed_ip = self.parse_lookup_ip(ip_address)?;

        self.parse_and_call_with_path(path, |path_elements| {
            let reader = self.reader.load();
            let reader = reader
                .as_ref()
                .ok_or_else(|| PyValueError::new_err(ERR_CLOSED_DB))?;

            self.lookup_result_to_python(py, reader.lookup_path(parsed_ip, path_elements))
        })
    }

    /// Query the database for information about an IP address and return the network prefix length.
    ///
    /// Args:
    ///     ip_address: The IP address to look up. May be a string (e.g., '1.2.3.4')
    ///         or an ipaddress.IPv4Address or ipaddress.IPv6Address object.
    ///
    /// Returns:
    ///     A tuple of (record, prefix_length) where record is the database record
    ///     value (or None if not found), and prefix_length is an integer representing
    ///     the network prefix length associated with the record.
    ///
    /// Raises:
    ///     ValueError: If the database has been closed or the IP address is invalid.
    ///     InvalidDatabaseError: If the database data is corrupt or invalid.
    ///
    /// Example:
    ///     >>> reader = maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb')
    ///     >>> record, prefix_len = reader.get_with_prefix_len('8.8.8.8')
    ///     >>> prefix_len
    ///     24
    fn get_with_prefix_len(
        &self,
        py: Python,
        ip_address: &Bound<'_, PyAny>,
    ) -> PyResult<(Py<PyAny>, usize)> {
        let parsed_ip = self.parse_lookup_ip(ip_address)?;

        let reader = self.reader.load();
        let reader = reader
            .as_ref()
            .ok_or_else(|| PyValueError::new_err(ERR_CLOSED_DB))?;

        self.lookup_prefix_result_to_python(py, reader.lookup_prefix(parsed_ip))
    }

    /// Query the database for multiple IP addresses in a single batch operation.
    ///
    /// This is an extension method not available in the original maxminddb module.
    /// It provides better performance than calling get() repeatedly by reducing
    /// call overhead. This method does not release the Python GIL while
    /// processing the batch.
    ///
    /// Args:
    ///     ips: An iterable of IP address strings or ipaddress objects to look up
    ///         (e.g., ['1.2.3.4', '8.8.8.8']).
    ///
    /// Returns:
    ///     A list of database record values for each IP address.
    ///     Elements will be None for IP addresses not found in the database.
    ///     The order of results matches the order of input IPs.
    ///
    /// Raises:
    ///     ValueError: If the database has been closed or any IP address is invalid.
    ///     InvalidDatabaseError: If the database data is corrupt or invalid.
    ///
    /// Example:
    ///     >>> reader = maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb')
    ///     >>> ips = ['8.8.8.8', '1.1.1.1', '208.67.222.222']
    ///     >>> results = reader.get_many(ips)
    ///     >>> len(results)
    ///     3
    ///
    /// Notes:
    ///     This method keeps the GIL for the duration of the batch. For better
    ///     concurrency with other Python threads, consider smaller batch sizes,
    ///     per-item lookups, or running lookups in a separate worker thread.
    fn get_many(&self, py: Python, ips: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        // Keep work under the GIL for lower per-item overhead; callers wanting
        // concurrent Python execution should prefer smaller batches or threading.
        let reader = self.reader.load();
        let reader = reader
            .as_ref()
            .ok_or_else(|| PyValueError::new_err(ERR_CLOSED_DB))?;
        reject_str_ips(ips)?;

        self.lookup_many(py, ips, |ip_addr| {
            self.lookup_result_to_python(py, reader.lookup(ip_addr))
        })
    }

    /// Query the database for a specific path for multiple IP addresses.
    ///
    /// This extension combines get_many() batching with get_path() selective
    /// decoding. It parses the path once and avoids decoding full records when
    /// only one field is needed.
    ///
    /// Args:
    ///     ips: An iterable of IP address strings or ipaddress objects to look up
    ///         (e.g., ['1.2.3.4', '8.8.8.8']).
    ///     path: A sequence (tuple or list) of strings or integers representing the
    ///         path to the data.
    ///
    /// Returns:
    ///     A list of values at the specified path. Elements will be None for IP
    ///     addresses or paths not found in the database.
    ///
    /// Raises:
    ///     ValueError: If the database has been closed or any IP address is invalid.
    ///     TypeError: If the path or IP iterable is invalid.
    ///     InvalidDatabaseError: If the database data is corrupt or invalid.
    fn get_many_path(
        &self,
        py: Python,
        ips: &Bound<'_, PyAny>,
        path: &Bound<'_, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        let reader = self.reader.load();
        let reader = reader
            .as_ref()
            .ok_or_else(|| PyValueError::new_err(ERR_CLOSED_DB))?;
        reject_str_ips(ips)?;

        self.parse_and_call_with_path(path, |path_elements| {
            self.lookup_many(py, ips, |ip_addr| {
                self.lookup_result_to_python(py, reader.lookup_path(ip_addr, path_elements))
            })
        })
    }

    /// Get metadata about the MaxMind DB database.
    ///
    /// Returns:
    ///     A Metadata object containing information about the database including:
    ///     - database_type: The type of database (e.g., 'GeoIP2-City')
    ///     - binary_format_major_version: Major version of the binary format
    ///     - binary_format_minor_version: Minor version of the binary format
    ///     - build_epoch: Unix timestamp when the database was built
    ///     - ip_version: 4 for IPv4-only, 6 for databases supporting both IPv4 and IPv6
    ///     - node_count: Number of nodes in the search tree
    ///     - record_size: Record size in bits (24, 28, or 32)
    ///     - description: Dictionary of locale codes to database descriptions
    ///     - languages: List of supported locale codes
    ///
    /// Raises:
    ///     OSError: If the database has been closed.
    ///
    /// Example:
    ///     >>> reader = maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb')
    ///     >>> metadata = reader.metadata()
    ///     >>> metadata.database_type
    ///     'GeoIP2-City'
    ///     >>> metadata.ip_version
    ///     6
    fn metadata(&self, _py: Python) -> PyResult<Metadata> {
        let reader = self.reader.load();
        let reader = reader
            .as_ref()
            .ok_or_else(|| PyOSError::new_err(ERR_CLOSED_DB))?;

        let meta = reader.metadata();

        Ok(Metadata::from_maxmind(meta))
    }

    /// Close the database and release resources.
    ///
    /// Closes the MaxMind DB file handle and releases associated resources.
    /// After calling this method, attempting to call get() or other query
    /// methods will raise a ValueError.
    ///
    /// Example:
    ///     >>> reader = maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb')
    ///     >>> reader.close()
    ///     >>> reader.get('8.8.8.8')  # Raises ValueError
    fn close(&self) {
        self.reader.store(None);
    }

    /// Enter the context manager (for use with 'with' statement).
    ///
    /// Returns:
    ///     The Reader object itself.
    ///
    /// Raises:
    ///     ValueError: If attempting to reopen a closed database.
    ///
    /// Example:
    ///     >>> with maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb') as reader:
    ///     ...     result = reader.get('8.8.8.8')
    fn __enter__(slf: Py<Self>, py: Python) -> PyResult<Py<Self>> {
        if slf.borrow(py).reader.load().is_none() {
            return Err(PyValueError::new_err(
                "Attempt to reopen a closed MaxMind DB",
            ));
        }
        Ok(slf)
    }

    /// Exit the context manager (for use with 'with' statement).
    ///
    /// Automatically closes the database when exiting the 'with' block.
    fn __exit__(
        &self,
        _exc_type: &Bound<'_, PyAny>,
        _exc_val: &Bound<'_, PyAny>,
        _exc_tb: &Bound<'_, PyAny>,
    ) {
        self.close();
    }

    /// Iterate over all networks in the database.
    ///
    /// Returns an iterator that yields (network, data) tuples for all networks
    /// in the database. Networks are represented as ipaddress.IPv4Network or
    /// ipaddress.IPv6Network objects.
    ///
    /// Returns:
    ///     An iterator yielding tuples of (network, record) for each entry.
    ///
    /// Raises:
    ///     ValueError: If the database has been closed.
    ///
    /// Example:
    ///     >>> reader = maxminddb_rust.open_database('/path/to/GeoLite2-Country.mmdb')
    ///     >>> for network, data in reader:
    ///     ...     print(f"{network}: {data['country']['iso_code']}")
    ///     ...     break
    ///     1.0.0.0/24: AU
    fn __iter__(slf: PyRef<'_, Self>) -> PyResult<ReaderIterator> {
        let reader = slf.get_reader()?;
        ReaderIterator::new(slf.py(), reader)
    }
}

// Internal helper methods for Reader
impl Reader {
    #[inline]
    fn parse_lookup_ip(&self, ip_address: &Bound<'_, PyAny>) -> PyResult<IpAddr> {
        let parsed_ip = parse_ip_address(ip_address)?;
        self.validate_lookup_ip(parsed_ip)
    }

    #[inline]
    fn validate_lookup_ip(&self, parsed_ip: IpAddr) -> PyResult<IpAddr> {
        if self.ip_version == 4 && matches!(parsed_ip, IpAddr::V6(_)) {
            Err(PyValueError::new_err(ipv6_in_ipv4_error(&parsed_ip)))
        } else {
            Ok(parsed_ip)
        }
    }

    #[inline]
    fn lookup_result_to_python(
        &self,
        py: Python,
        result: Result<Option<PyDecodedValue>, MaxMindDbError>,
    ) -> PyResult<Py<PyAny>> {
        match result {
            Ok(Some(data)) => Ok(data.into_py()),
            Ok(None) => Ok(py.None()),
            Err(err) => Err(Self::lookup_error(err)),
        }
    }

    #[inline]
    fn lookup_prefix_result_to_python(
        &self,
        py: Python,
        result: Result<(Option<PyDecodedValue>, usize), MaxMindDbError>,
    ) -> PyResult<(Py<PyAny>, usize)> {
        match result {
            Ok((Some(data), prefix_len)) => Ok((data.into_py(), prefix_len)),
            Ok((None, prefix_len)) => Ok((py.None(), prefix_len)),
            Err(err) => Err(Self::lookup_error(err)),
        }
    }

    #[inline]
    fn lookup_error(err: MaxMindDbError) -> PyErr {
        match err {
            MaxMindDbError::InvalidDatabase { .. }
            | MaxMindDbError::Decoding { .. }
            | MaxMindDbError::ResourceLimit { .. } => InvalidDatabaseError::new_err(ERR_BAD_DATA),
            other => PyValueError::new_err(format!("Database error: {other}")),
        }
    }

    fn extract_database_path(database: &Bound<'_, PyAny>) -> PyResult<PathBuf> {
        let path = if database.is_instance_of::<PyString>() {
            database.clone()
        } else {
            match database.call_method0("__fspath__") {
                Ok(path) => path,
                Err(err) if err.is_instance_of::<PyAttributeError>(database.py()) => {
                    return Err(PyValueError::new_err(ERR_BAD_DATABASE_ARG));
                }
                Err(err) => return Err(err),
            }
        };

        if let Ok(path) = path.extract::<String>() {
            return Ok(path.into());
        }
        path.extract::<OsString>().map(PathBuf::from)
    }

    fn resolve_open_mode(mode: i32) -> PyResult<OpenMode> {
        OpenMode::try_from(mode)
    }

    fn open_reader_from_mode(database: &Bound<'_, PyAny>, mode: OpenMode) -> PyResult<Self> {
        match mode {
            OpenMode::Mmap | OpenMode::MmapExt => {
                let path = Self::extract_database_path(database)?;
                open_database_mmap(&path)
            }
            OpenMode::File | OpenMode::Memory => {
                let path = Self::extract_database_path(database)?;
                open_database_memory(&path)
            }
            OpenMode::Fd => open_database_fd(database),
        }
    }

    #[inline]
    fn parse_and_call_with_path<R>(
        &self,
        path: &Bound<'_, PyAny>,
        f: impl FnOnce(&[PathElement<'_>]) -> PyResult<R>,
    ) -> PyResult<R> {
        // Cache exact tuples only. The cache matches tuples by their storage,
        // which a subclass that overrides `__iter__` would not agree with.
        if let Ok(path_tuple) = path.cast_exact::<PyTuple>() {
            let owned_path = Self::get_or_parse_tuple_path(path_tuple)?;
            return Self::call_with_owned_path(&owned_path, f);
        }

        let owned_path = parse_path(path)?;
        Self::call_with_owned_path(&owned_path, f)
    }

    #[inline]
    fn get_or_parse_tuple_path(
        path_tuple: &Bound<'_, PyTuple>,
    ) -> PyResult<Arc<Vec<OwnedPathElement>>> {
        if let Some(cached) = PATH_CACHE.with(|cache| cache.borrow().find(path_tuple)) {
            return Ok(cached);
        }

        let parsed = Arc::new(parse_path(path_tuple.as_any())?);
        let evicted = PATH_CACHE.with(|cache| match cache.try_borrow_mut() {
            Ok(mut cache) => cache.insert((path_tuple.clone().unbind(), Arc::clone(&parsed))),
            // `find` holds a shared borrow while matching, and a failed match
            // can create and discard a Python exception. That allocation can
            // start a GC pass, and a finalizer that looks up a path then gets
            // here with the borrow still held. Skip caching that path.
            Err(_) => None,
        });
        drop(evicted);
        Ok(parsed)
    }

    #[inline]
    fn call_with_owned_path<R>(
        owned_path: &[OwnedPathElement],
        f: impl FnOnce(&[PathElement<'_>]) -> PyResult<R>,
    ) -> PyResult<R> {
        let mut stack_buf: [PathElement<'_>; 8] = [const { PathElement::Index(0) }; 8];
        if owned_path.len() <= stack_buf.len() {
            for (dst, src) in stack_buf.iter_mut().zip(owned_path.iter()) {
                *dst = src.as_path_element();
            }
            f(&stack_buf[..owned_path.len()])
        } else {
            let path_elements = path_elements_from_owned_path(owned_path);
            f(&path_elements)
        }
    }

    /// Get the reader from the internal mutex, returning an error if closed
    #[inline]
    fn get_reader(&self) -> PyResult<Arc<ReaderSource>> {
        self.reader
            .load_full()
            .ok_or_else(|| PyValueError::new_err(ERR_CLOSED_DB))
    }

    /// Look up each address in `ips` and return the results as a list.
    fn lookup_many(
        &self,
        py: Python<'_>,
        ips: &Bound<'_, PyAny>,
        mut lookup: impl FnMut(IpAddr) -> PyResult<Py<PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let mut objects;
        if let Ok(list) = ips.cast::<PyList>() {
            objects = Vec::with_capacity(list.len());
            for ip in list.iter() {
                objects.push(lookup(self.parse_lookup_ip(&ip)?)?);
            }
        } else if let Ok(tuple) = ips.cast::<PyTuple>() {
            objects = Vec::with_capacity(tuple.len());
            for ip in tuple.as_slice() {
                objects.push(lookup(self.parse_lookup_ip(ip)?)?);
            }
        } else {
            let iterator = ips
                .try_iter()
                .map_err(|_| PyTypeError::new_err(ERR_IPS_ITERABLE))?;
            objects = Vec::with_capacity(ips.len().unwrap_or(0));
            for ip in iterator {
                objects.push(lookup(self.parse_lookup_ip(&ip?)?)?);
            }
        }
        Ok(PyList::new(py, objects)?.into_any().unbind())
    }
}

/// Reject a str or bytes-like `ips`, which would otherwise iterate by character.
#[inline]
fn reject_str_ips(ips: &Bound<'_, PyAny>) -> PyResult<()> {
    if is_bytes_like_or_string(ips) {
        return Err(PyTypeError::new_err(ERR_IPS_ITERABLE));
    }
    Ok(())
}

#[inline]
fn is_bytes_like_or_string(obj: &Bound<'_, PyAny>) -> bool {
    obj.is_instance_of::<PyString>()
        || obj.is_instance_of::<PyBytes>()
        || obj.is_instance_of::<PyByteArray>()
}

/// Open a MaxMind DB database file.
///
/// Args:
///     database: Path to the MaxMind DB file, or a readable binary object for MODE_FD.
///         Raw integer OS file descriptors are not accepted.
///     mode: The mode to use when opening the database. Defaults to MODE_AUTO.
///         Available modes:
///         - MODE_AUTO (0): Currently resolves to MODE_MMAP
///         - MODE_MMAP (2): Use memory-mapped file I/O (default, best performance)
///         - MODE_MMAP_EXT (1): Compatibility alias for the same Rust mmap
///           reader as MODE_MMAP
///         - MODE_FILE (4): Read the database file into memory
///         - MODE_MEMORY (8): Load entire database into memory
///         - MODE_FD (16): Read bytes from a file-like object into memory
///
/// Returns:
///     A Reader object that can be used to query the database.
///
/// Raises:
///     FileNotFoundError: If the database file does not exist.
///     IOError: If the database file cannot be read or memory-mapped.
///     InvalidDatabaseError: If the file is not a valid MaxMind DB file.
///     ValueError: If an unsupported mode is specified.
///
/// Example:
///     >>> import maxminddb_rust
///     >>> reader = maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb')
///     >>> reader.get('8.8.8.8')
///     {'city': {'names': {'en': 'Mountain View'}}, ...}
///     >>> reader.close()
///
///     >>> # Using context manager
///     >>> with maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb') as reader:
///     ...     result = reader.get('8.8.8.8')
///
///     >>> # Specify mode explicitly
///     >>> reader = maxminddb_rust.open_database('/path/to/GeoIP2-City.mmdb',
///     ...                                   mode=maxminddb_rust.MODE_MEMORY)
#[pyfunction]
#[pyo3(signature = (database, mode=MODE_AUTO))]
fn open_database(database: &Bound<'_, PyAny>, mode: i32) -> PyResult<Reader> {
    Reader::new(database, mode)
}

/// Python module definition
#[pymodule]
fn maxminddb_rust(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Add classes
    m.add_class::<Reader>()?;
    m.add_class::<Metadata>()?;

    // Add exception
    m.add(
        "InvalidDatabaseError",
        _py.get_type::<InvalidDatabaseError>(),
    )?;

    // Add function
    m.add_function(wrap_pyfunction!(open_database, m)?)?;

    // Add MODE constants
    m.add("MODE_AUTO", MODE_AUTO)?;
    m.add("MODE_MMAP_EXT", MODE_MMAP_EXT)?;
    m.add("MODE_MMAP", MODE_MMAP)?;
    m.add("MODE_FILE", MODE_FILE)?;
    m.add("MODE_MEMORY", MODE_MEMORY)?;
    m.add("MODE_FD", MODE_FD)?;

    Ok(())
}
