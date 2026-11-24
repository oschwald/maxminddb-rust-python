use ::maxminddb as maxminddb_crate;
use arc_swap::ArcSwapOption;
use maxminddb_crate::{MaxMindDbError, PathElement, Reader as MaxMindReader};
use memmap2::Mmap;
use pyo3::{
    exceptions::{
        PyAttributeError, PyOSError, PyOverflowError, PyRuntimeError, PyTypeError, PyValueError,
    },
    prelude::*,
    types::{
        PyByteArray, PyBytes, PyDict, PyList, PyListMethods, PyModule, PyString, PyTuple,
        PyTupleMethods,
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
use open::{
    has_callable_read, open_database_fd, open_database_memory, open_database_mmap,
    unsupported_database_type,
};
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
const ERR_IPS_ITERABLE: &str = "ips must be an iterable of strings or ipaddress objects";

#[derive(Copy, Clone, Debug, Eq, PartialEq)]
enum OpenMode {
    Auto,
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
            MODE_AUTO => Ok(Self::Auto),
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

/// The `mode` argument of `Reader()` and `open_database()`.
///
/// The official package checks the mode with `in`, so a value that equals a
/// mode, such as 2.0, is accepted, and any other value, such as None, raises
/// ValueError. Extraction fails only when Python raises an unexpected error.
struct ModeArg(Result<i32, String>);

impl ModeArg {
    const AUTO: Self = Self(Ok(MODE_AUTO));

    fn resolve(self) -> PyResult<OpenMode> {
        match self.0 {
            Ok(mode) => OpenMode::try_from(mode),
            Err(repr) => Err(PyValueError::new_err(format!(
                "Unsupported open mode ({repr})"
            ))),
        }
    }
}

impl<'a, 'py> FromPyObject<'a, 'py> for ModeArg {
    type Error = PyErr;

    fn extract(obj: Borrowed<'a, 'py, PyAny>) -> PyResult<Self> {
        let py = obj.py();
        match obj.extract::<i32>() {
            Ok(mode) => Ok(Self(Ok(mode))),
            Err(err)
                if err.is_instance_of::<PyTypeError>(py)
                    || err.is_instance_of::<PyOverflowError>(py) =>
            {
                for mode in [
                    MODE_AUTO,
                    MODE_MMAP_EXT,
                    MODE_MMAP,
                    MODE_FILE,
                    MODE_MEMORY,
                    MODE_FD,
                ] {
                    if obj.eq(mode)? {
                        return Ok(Self(Ok(mode)));
                    }
                }
                Ok(Self(Err(obj.repr()?.to_string())))
            }
            Err(err) => Err(err),
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
/// Supports memory-mapped files (MODE_AUTO/MODE_MMAP/MODE_MMAP_EXT) and read-file
/// modes (MODE_FILE/MODE_MEMORY/MODE_FD). MODE_AUTO memory-maps a path and reads
/// a binary file object into memory.
#[pyclass(module = "maxminddb_rust")]
struct Reader {
    reader: ArcSwapOption<ReaderSource>,
    ip_version: u16,
}

#[pymethods]
impl Reader {
    #[new]
    #[pyo3(signature = (database, mode=ModeArg::AUTO))]
    fn new(database: &Bound<'_, PyAny>, mode: ModeArg) -> PyResult<Self> {
        Self::open_reader_from_mode(database, mode.resolve()?)
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
    /// processing the batch. It runs signal handlers every few thousand
    /// addresses, so Ctrl-C can interrupt a large batch.
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
    /// only one field is needed. Like get_many(), it runs signal handlers every
    /// few thousand addresses.
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
    /// ipaddress.IPv6Network objects. Threads can share one iterator. Each
    /// network is then yielded to only one of them.
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
            Err(err) => Err(Self::lookup_error(py, err)),
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
            Err(err) => Err(Self::lookup_error(py, err)),
        }
    }

    /// Convert a failed lookup to a Python exception.
    ///
    /// Bad data raises `InvalidDatabaseError` with the message the official
    /// reader uses. Its `__cause__` holds the decoder's own message, which in
    /// turn has the Python error that stopped decoding, if one did, as its
    /// cause. A `MemoryError` from decoding is raised as it is.
    #[cold]
    fn lookup_error(py: Python, err: MaxMindDbError) -> PyErr {
        let py_cause = match decode::take_decode_failure(py) {
            Ok(cause) => cause,
            Err(py_err) => return py_err,
        };
        match err {
            MaxMindDbError::InvalidDatabase { .. }
            | MaxMindDbError::Decoding { .. }
            | MaxMindDbError::ResourceLimit { .. } => {
                let detail = InvalidDatabaseError::new_err(err.to_string());
                detail.set_cause(py, py_cause);
                let py_err = InvalidDatabaseError::new_err(ERR_BAD_DATA);
                py_err.set_cause(py, Some(detail));
                py_err
            }
            other => {
                let py_err = PyValueError::new_err(format!("Database error: {other}"));
                py_err.set_cause(py, py_cause);
                py_err
            }
        }
    }

    /// Return the path of a str, bytes or os.PathLike database argument, or
    /// None for any other type. Errors from `__fspath__` propagate.
    fn database_path(database: &Bound<'_, PyAny>) -> PyResult<Option<PathBuf>> {
        let path = if database.is_instance_of::<PyString>() || database.is_instance_of::<PyBytes>()
        {
            database.clone()
        } else {
            match database.getattr(pyo3::intern!(database.py(), "__fspath__")) {
                Ok(fspath) => fspath.call0()?,
                Err(err) if err.is_instance_of::<PyAttributeError>(database.py()) => {
                    return Ok(None);
                }
                Err(err) => return Err(err),
            }
        };

        if let Ok(path) = path.cast::<PyString>() {
            if let Ok(path) = path.to_str() {
                return Ok(Some(path.into()));
            }
            return path.extract::<OsString>().map(|path| Some(path.into()));
        }
        if path.is_instance_of::<PyBytes>() {
            // os.fsdecode() gives the str that open() would use for the bytes.
            let decoded = database
                .py()
                .import(pyo3::intern!(database.py(), "os"))?
                .call_method1(pyo3::intern!(database.py(), "fsdecode"), (path,))?;
            return decoded.extract::<OsString>().map(|path| Some(path.into()));
        }
        Err(PyTypeError::new_err(format!(
            "expected {}.__fspath__() to return str or bytes, not {}",
            database.get_type().name()?,
            path.get_type().name()?
        )))
    }

    fn open_reader_from_mode(database: &Bound<'_, PyAny>, mode: OpenMode) -> PyResult<Self> {
        // A path wins over read(), because some path objects also have a text
        // read(). MODE_FD reads any object with read().
        if mode != OpenMode::Fd {
            if let Some(path) = Self::database_path(database)? {
                return match mode {
                    OpenMode::Auto | OpenMode::Mmap | OpenMode::MmapExt => {
                        open_database_mmap(&path)
                    }
                    OpenMode::File | OpenMode::Memory => open_database_memory(&path),
                    OpenMode::Fd => unreachable!("MODE_FD does not take a path"),
                };
            }
        }
        // MODE_AUTO reads a file object into memory, as MODE_FD does.
        if matches!(mode, OpenMode::Auto | OpenMode::Fd) && has_callable_read(database)? {
            return open_database_fd(database);
        }
        Err(unsupported_database_type(database, mode))
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
        // The fast paths read list and tuple storage directly. Exact types
        // only: a subclass may override `__iter__`, so it takes the iterator
        // path below.
        if let Ok(list) = ips.cast_exact::<PyList>() {
            objects = Vec::with_capacity(list.len());
            for (index, ip) in list.iter().enumerate() {
                check_signals_every(py, index)?;
                objects.push(lookup(self.parse_lookup_ip(&ip)?)?);
            }
        } else if let Ok(tuple) = ips.cast_exact::<PyTuple>() {
            objects = Vec::with_capacity(tuple.len());
            for (index, ip) in tuple.as_slice().iter().enumerate() {
                check_signals_every(py, index)?;
                objects.push(lookup(self.parse_lookup_ip(ip)?)?);
            }
        } else {
            // Replace a TypeError, such as "not iterable", with a clearer
            // message and keep the original as its cause. Keep any other
            // error that `__iter__` raises as it is.
            let iterator = ips.try_iter().map_err(|err| {
                if err.is_instance_of::<PyTypeError>(py) {
                    let type_err = PyTypeError::new_err(ERR_IPS_ITERABLE);
                    type_err.set_cause(py, Some(err));
                    type_err
                } else {
                    err
                }
            })?;
            // Do not pre-size from `__len__`: a user iterable can report any
            // length, and a huge one would overflow or exhaust the allocation.
            objects = Vec::new();
            for (index, ip) in iterator.enumerate() {
                check_signals_every(py, index)?;
                objects.push(lookup(self.parse_lookup_ip(&ip?)?)?);
            }
        }
        Ok(PyList::new(py, objects)?.into_any().unbind())
    }
}

/// How many addresses a batch lookup handles between checks for signals.
const SIGNAL_CHECK_INTERVAL: usize = 4096;

/// Run pending signal handlers, such as the `KeyboardInterrupt` for Ctrl-C,
/// every `SIGNAL_CHECK_INTERVAL` items. A batch of strings runs no Python code,
/// so Python would otherwise handle the signal only after the whole batch.
#[inline]
fn check_signals_every(py: Python<'_>, index: usize) -> PyResult<()> {
    if index % SIGNAL_CHECK_INTERVAL == SIGNAL_CHECK_INTERVAL - 1 {
        py.check_signals()?;
    }
    Ok(())
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
///     database: Path to the MaxMind DB file (str, bytes or os.PathLike), or a
///         readable binary file object for MODE_AUTO or MODE_FD. Raw integer OS
///         file descriptors are not accepted.
///     mode: The mode to use when opening the database. Defaults to MODE_AUTO.
///         Available modes:
///         - MODE_AUTO (0): Memory-map a path, or read a file object into memory
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
///     OSError: If the database file cannot be opened, read or memory-mapped.
///         OS errors set ``errno`` and ``filename``, so the subclass
///         matches the cause, as with ``open()``.
///     InvalidDatabaseError: If the file is not a valid MaxMind DB file.
///     TypeError: If the database argument is not supported by the mode.
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
#[pyo3(signature = (database, mode=ModeArg::AUTO))]
fn open_database(database: &Bound<'_, PyAny>, mode: ModeArg) -> PyResult<Reader> {
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

    // Add the Mode enum and the MODE constants, which are its members as in
    // the official package.
    let modes = [
        ("AUTO", MODE_AUTO),
        ("MMAP_EXT", MODE_MMAP_EXT),
        ("MMAP", MODE_MMAP),
        ("FILE", MODE_FILE),
        ("MEMORY", MODE_MEMORY),
        ("FD", MODE_FD),
    ];
    let kwargs = PyDict::new(_py);
    kwargs.set_item("module", "maxminddb_rust")?;
    let mode_enum = _py
        .import("enum")?
        .getattr("IntEnum")?
        .call(("Mode", modes.to_vec()), Some(&kwargs))?;
    mode_enum.setattr(
        "__doc__",
        "Database open modes.\n\nThese modes control how the MaxMind DB file is opened and read.",
    )?;
    m.add("Mode", &mode_enum)?;
    for (name, _) in modes {
        m.add(format!("MODE_{name}"), mode_enum.getattr(name)?)?;
    }

    // The package maturin builds star-imports this module, so __version__ must
    // be in __all__ to be visible, unlike in the official package.
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;

    Ok(())
}
