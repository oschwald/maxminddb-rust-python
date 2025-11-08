use ::maxminddb as maxminddb_crate;
use maxminddb_crate::{MaxMindDbError, Reader as MaxMindReader, Within, WithinItem};
use memmap2::Mmap;
use pyo3::{
    conversion::IntoPyObjectExt,
    exceptions::{
        PyFileNotFoundError, PyIOError, PyOSError, PyRuntimeError, PyTypeError, PyValueError,
    },
    prelude::*,
    types::{PyBytes, PyDict, PyList, PyModule, PyString},
};
use serde::de::{self, Deserialize, DeserializeSeed, Deserializer, MapAccess, SeqAccess, Visitor};
use std::{
    borrow::Cow,
    collections::BTreeMap,
    fmt,
    fs::File,
    io::Read as IoRead,
    mem::transmute,
    net::IpAddr,
    path::Path,
    str::FromStr,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, RwLock,
    },
};

// Define InvalidDatabaseError exception (subclass of RuntimeError)
pyo3::create_exception!(
    maxminddb_pyo3_exceptions,
    InvalidDatabaseError,
    PyRuntimeError,
    "Invalid MaxMind DB"
);

/// Wrapper that owns the Python object produced by deserializing a MaxMind record
#[derive(Debug)]
struct PyDecodedValue {
    value: Py<PyAny>,
}

impl PyDecodedValue {
    #[inline]
    fn new(value: Py<PyAny>) -> Self {
        Self { value }
    }

    #[inline]
    fn into_py(self) -> Py<PyAny> {
        self.value
    }
}

impl<'de> Deserialize<'de> for PyDecodedValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        Python::attach(|py| PyValueSeed { py }.deserialize(deserializer))
    }
}

#[derive(Copy, Clone)]
struct PyValueSeed<'py> {
    py: Python<'py>,
}

impl<'py> PyValueSeed<'py> {
    #[inline]
    fn new(py: Python<'py>) -> Self {
        Self { py }
    }
}

impl<'de, 'py> DeserializeSeed<'de> for PyValueSeed<'py> {
    type Value = PyDecodedValue;

    fn deserialize<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(PyValueVisitor { py: self.py })
    }
}

struct PyValueVisitor<'py> {
    py: Python<'py>,
}

impl<'de, 'py> Visitor<'de> for PyValueVisitor<'py> {
    type Value = PyDecodedValue;

    fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
        formatter.write_str("any valid MaxMind DB value")
    }

    fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_i32<E>(self, value: i32) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        if value >= i32::MIN as i64 && value <= i32::MAX as i64 {
            bound_to_value((value as i32).into_py_any(self.py))
        } else {
            Err(E::custom(format!("integer {} out of i32 range", value)))
        }
    }

    fn visit_u16<E>(self, value: u16) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_u32<E>(self, value: u32) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_u128<E>(self, value: u128) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_f32<E>(self, value: f32) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_string<E>(self, value: String) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        bound_to_value(value.into_py_any(self.py))
    }

    fn visit_bytes<E>(self, value: &[u8]) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        let py_bytes = PyBytes::new(self.py, value);
        Ok(PyDecodedValue::new(py_bytes.into_any().unbind()))
    }

    fn visit_byte_buf<E>(self, value: Vec<u8>) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        let py_bytes = PyBytes::new(self.py, &value);
        Ok(PyDecodedValue::new(py_bytes.into_any().unbind()))
    }

    fn visit_seq<A>(self, mut seq: A) -> Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let capacity = seq.size_hint().unwrap_or(0);
        let mut elements = Vec::with_capacity(capacity);
        while let Some(elem) = seq.next_element_seed(PyValueSeed::new(self.py))? {
            elements.push(elem.into_py());
        }
        let py_list = PyList::new(self.py, elements).map_err(pyerr_to_de_error)?;
        Ok(PyDecodedValue::new(py_list.into_any().unbind()))
    }

    fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let dict = PyDict::new(self.py);
        while let Some(key) = map.next_key::<Cow<'de, str>>()? {
            let value = map.next_value_seed(PyValueSeed::new(self.py))?;
            dict.set_item(key.as_ref(), value.into_py())
                .map_err(pyerr_to_de_error)?;
        }
        Ok(PyDecodedValue::new(dict.into_any().unbind()))
    }
}

fn pyerr_to_de_error<E: de::Error>(err: PyErr) -> E {
    E::custom(err.to_string())
}

fn bound_to_value<E: de::Error>(result: PyResult<Py<PyAny>>) -> Result<PyDecodedValue, E> {
    result.map(PyDecodedValue::new).map_err(pyerr_to_de_error)
}

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

/// Enum to handle different reader source types
enum ReaderSource {
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
            ReaderSource::Mmap(reader) => reader.lookup(ip),
            ReaderSource::Memory(reader) => reader.lookup(ip),
        }
    }

    #[inline]
    fn lookup_prefix(
        &self,
        ip: IpAddr,
    ) -> Result<(Option<PyDecodedValue>, usize), maxminddb_crate::MaxMindDbError> {
        match self {
            ReaderSource::Mmap(reader) => reader.lookup_prefix(ip),
            ReaderSource::Memory(reader) => reader.lookup_prefix(ip),
        }
    }

    #[inline]
    fn metadata(&self) -> &maxminddb_crate::Metadata {
        match self {
            ReaderSource::Mmap(reader) => &reader.metadata,
            ReaderSource::Memory(reader) => &reader.metadata,
        }
    }
}

/// Metadata about the MaxMind DB database
#[pyclass(module = "maxminddb.extension")]
struct Metadata {
    #[pyo3(get)]
    binary_format_major_version: u16,
    #[pyo3(get)]
    binary_format_minor_version: u16,
    #[pyo3(get)]
    build_epoch: u64,
    #[pyo3(get)]
    database_type: String,
    description_dict: BTreeMap<String, String>,
    #[pyo3(get)]
    ip_version: u16,
    languages_list: Vec<String>,
    #[pyo3(get)]
    node_count: u32,
    #[pyo3(get)]
    record_size: u16,
}

#[pymethods]
impl Metadata {
    #[getter]
    fn description(&self, py: Python) -> PyResult<Py<PyAny>> {
        // Convert BTreeMap<String, String> to Python dict
        let dict = PyDict::new(py);
        for (k, v) in &self.description_dict {
            dict.set_item(k, v)?;
        }
        Ok(dict.into())
    }

    #[getter]
    fn languages(&self, py: Python) -> PyResult<Py<PyAny>> {
        // Convert Vec<String> to Python list
        let list = PyList::new(py, &self.languages_list)?;
        Ok(list.into_any().unbind())
    }

    #[getter]
    fn node_byte_size(&self) -> u16 {
        self.record_size / 4
    }

    #[getter]
    fn search_tree_size(&self) -> u32 {
        self.node_count * (self.record_size as u32 / 4)
    }
}

/// A Python wrapper around the MaxMind DB reader.
/// Supports both memory-mapped files (MODE_MMAP) and in-memory (MODE_MEMORY) modes.
#[pyclass(module = "maxminddb.extension")]
struct Reader {
    reader: Arc<RwLock<Option<Arc<ReaderSource>>>>,
    closed: Arc<AtomicBool>,
    ip_version: u16,
}

#[pymethods]
impl Reader {
    #[new]
    #[pyo3(signature = (database, mode=MODE_AUTO))]
    fn new(database: &Bound<'_, PyAny>, mode: i32) -> PyResult<Self> {
        // Extract path from database parameter
        let path = if let Ok(s) = database.extract::<String>() {
            s
        } else {
            // Try to get __fspath__ for PathLike objects
            match database.call_method0("__fspath__") {
                Ok(fspath) => fspath.extract::<String>()?,
                Err(_) => {
                    return Err(PyValueError::new_err(
                        "database must be a string, PathLike, or file descriptor",
                    ));
                }
            }
        };

        // Determine which mode to use
        let actual_mode = if mode == MODE_AUTO {
            MODE_MMAP // Default to mmap for best performance
        } else {
            mode
        };

        // Validate and open database with appropriate mode
        match actual_mode {
            MODE_MMAP | MODE_MMAP_EXT => open_database_mmap(&path),
            MODE_MEMORY => open_database_memory(&path),
            MODE_FILE => Err(PyValueError::new_err(
                "MODE_FILE not yet supported, use MODE_MMAP, MODE_MMAP_EXT, or MODE_MEMORY",
            )),
            MODE_FD => Err(PyValueError::new_err(
                "MODE_FD not yet supported, use MODE_MMAP, MODE_MMAP_EXT, or MODE_MEMORY",
            )),
            _ => Err(PyValueError::new_err(format!(
                "Unsupported open mode ({actual_mode})"
            ))),
        }
    }

    #[getter]
    fn closed(&self) -> bool {
        self.closed.load(Ordering::Relaxed)
    }

    #[inline]
    fn get(&self, py: Python, ip_address: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
        // Quick check if database is closed
        if self.closed.load(Ordering::Acquire) {
            return Err(PyValueError::new_err(ERR_CLOSED_DB));
        }

        // Parse IP address - support string or ipaddress objects
        let parsed_ip = parse_ip_address(ip_address)?;

        if self.ip_version == 4 && matches!(parsed_ip.addr, IpAddr::V6(_)) {
            return Err(PyValueError::new_err(ipv6_in_ipv4_error(&parsed_ip.addr)));
        }

        let reader = self.get_reader()?;

        // Release GIL during lookup for better concurrency
        let lookup_result = py.detach(|| reader.lookup(parsed_ip.addr));

        match lookup_result {
            Ok(Some(data)) => Ok(data.into_py()),
            Ok(None) => Ok(py.None()),
            Err(MaxMindDbError::InvalidDatabase(_) | MaxMindDbError::Decoding(_)) => {
                Err(InvalidDatabaseError::new_err(ERR_BAD_DATA))
            }
            Err(err) => Err(PyValueError::new_err(format!("Database error: {err}"))),
        }
    }

    fn get_with_prefix_len(
        &self,
        py: Python,
        ip_address: &Bound<'_, PyAny>,
    ) -> PyResult<(Py<PyAny>, usize)> {
        // Quick check if database is closed
        if self.closed.load(Ordering::Acquire) {
            return Err(PyValueError::new_err(ERR_CLOSED_DB));
        }

        // Parse IP address - support string or ipaddress objects
        let parsed_ip = parse_ip_address(ip_address)?;

        if self.ip_version == 4 && matches!(parsed_ip.addr, IpAddr::V6(_)) {
            return Err(PyValueError::new_err(ipv6_in_ipv4_error(&parsed_ip.addr)));
        }

        let reader = self.get_reader()?;

        // Release GIL during lookup
        let result = py.detach(|| reader.lookup_prefix(parsed_ip.addr));

        match result {
            Ok((Some(data), prefix_len)) => {
                let py_obj = data.into_py();
                Ok((py_obj, prefix_len))
            }
            Ok((None, prefix_len)) => Ok((py.None(), prefix_len)),
            Err(MaxMindDbError::InvalidDatabase(_) | MaxMindDbError::Decoding(_)) => {
                Err(InvalidDatabaseError::new_err(ERR_BAD_DATA))
            }
            Err(err) => Err(PyValueError::new_err(format!("Database error: {err}"))),
        }
    }

    /// Batch lookup multiple IP addresses at once to reduce call overhead
    /// This is an extension method not in the original maxminddb module
    fn get_many(&self, py: Python, ips: Vec<String>) -> PyResult<Vec<Py<PyAny>>> {
        // Quick check if database is closed
        if self.closed.load(Ordering::Acquire) {
            return Err(PyValueError::new_err(ERR_CLOSED_DB));
        }

        let mut parsed_ips = Vec::with_capacity(ips.len());
        for ip in &ips {
            let ip_addr: IpAddr = ip
                .parse()
                .map_err(|_| PyValueError::new_err(format!("Invalid IP address: {ip}")))?;

            if self.ip_version == 4 && matches!(ip_addr, IpAddr::V6(_)) {
                return Err(PyValueError::new_err(ipv6_in_ipv4_error(&ip_addr)));
            }

            parsed_ips.push(ip_addr);
        }

        let reader = self.get_reader()?;

        // Release GIL during all lookups
        let results: Vec<Result<Option<PyDecodedValue>, MaxMindDbError>> =
            py.detach(|| parsed_ips.iter().map(|ip| reader.lookup(*ip)).collect());

        let mut objects = Vec::with_capacity(results.len());
        for result in results {
            match result {
                Ok(Some(data)) => objects.push(data.into_py()),
                Ok(None) => objects.push(py.None()),
                Err(MaxMindDbError::InvalidDatabase(_) | MaxMindDbError::Decoding(_)) => {
                    return Err(InvalidDatabaseError::new_err(ERR_BAD_DATA));
                }
                Err(err) => {
                    return Err(PyValueError::new_err(format!("Database error: {err}")));
                }
            }
        }

        Ok(objects)
    }

    /// Metadata about the database
    fn metadata(&self, _py: Python) -> PyResult<Metadata> {
        // Quick check if database is closed
        if self.closed.load(Ordering::Acquire) {
            return Err(PyOSError::new_err(ERR_CLOSED_DB));
        }

        let reader = self
            .get_reader()
            .map_err(|_| PyOSError::new_err(ERR_CLOSED_DB))?;

        let meta = reader.metadata();

        Ok(Metadata {
            binary_format_major_version: meta.binary_format_major_version,
            binary_format_minor_version: meta.binary_format_minor_version,
            build_epoch: meta.build_epoch,
            database_type: meta.database_type.clone(),
            description_dict: meta.description.clone(),
            ip_version: meta.ip_version,
            languages_list: meta.languages.clone(),
            node_count: meta.node_count,
            record_size: meta.record_size,
        })
    }

    /// Close the database and release resources
    fn close(&self) {
        // Set closed flag and clear the reader
        self.closed.store(true, Ordering::Release);
        if let Ok(mut guard) = self.reader.write() {
            *guard = None;
        }
    }

    /// Context manager entry
    fn __enter__(slf: Py<Self>, py: Python) -> PyResult<Py<Self>> {
        // Check if database is closed
        let is_closed = slf.borrow(py).closed.load(Ordering::Acquire);
        if is_closed {
            return Err(PyValueError::new_err(
                "Attempt to reopen a closed MaxMind DB",
            ));
        }
        Ok(slf)
    }

    /// Context manager exit
    fn __exit__(
        &self,
        _exc_type: &Bound<'_, PyAny>,
        _exc_val: &Bound<'_, PyAny>,
        _exc_tb: &Bound<'_, PyAny>,
    ) {
        self.close();
    }

    /// Iterate over all networks in the database
    fn __iter__(slf: PyRef<'_, Self>) -> PyResult<ReaderIterator> {
        // Check if database is closed
        if slf.closed.load(Ordering::Acquire) {
            return Err(PyValueError::new_err(ERR_CLOSED_DB));
        }

        let reader = slf.get_reader()?;
        ReaderIterator::new(slf.py(), reader)
    }
}

// Internal helper methods for Reader
impl Reader {
    /// Get the reader from the internal mutex, returning an error if closed
    #[inline]
    fn get_reader(&self) -> PyResult<Arc<ReaderSource>> {
        self.reader
            .read()
            .map_err(|_| PyValueError::new_err(ERR_CLOSED_DB))?
            .clone()
            .ok_or_else(|| PyValueError::new_err(ERR_CLOSED_DB))
    }
}

/// Iterator for Reader that yields (network, record) tuples
#[pyclass(module = "maxminddb.extension")]
struct ReaderIterator {
    _reader_guard: Arc<ReaderSource>,
    iter: ReaderWithin,
    ipv4_network_cls: Py<PyAny>,
    ipv6_network_cls: Py<PyAny>,
}

impl ReaderIterator {
    fn new(py: Python, reader: Arc<ReaderSource>) -> PyResult<Self> {
        let ip_version = reader.metadata().ip_version;

        // For IPv4 databases, iterate over IPv4 range only
        // For IPv6 databases, iterate over IPv6 range only (includes IPv4-mapped addresses)
        let (network_str, network_type) = if ip_version == 4 {
            ("0.0.0.0/0", "IPv4")
        } else {
            ("::/0", "IPv6")
        };

        let network = ipnetwork::IpNetwork::from_str(network_str).map_err(|e| {
            InvalidDatabaseError::new_err(format!(
                "Failed to create {} network: {}",
                network_type, e
            ))
        })?;

        let iter = ReaderWithin::new(&reader, network).map_err(|e| {
            InvalidDatabaseError::new_err(format!("Failed to iterate {}: {}", network_type, e))
        })?;

        let ipaddress = py.import("ipaddress")?;
        let ipv4_network_cls = ipaddress.getattr("IPv4Network")?.unbind();
        let ipv6_network_cls = ipaddress.getattr("IPv6Network")?.unbind();

        Ok(Self {
            _reader_guard: reader,
            iter,
            ipv4_network_cls,
            ipv6_network_cls,
        })
    }
}

#[pymethods]
impl ReaderIterator {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self, py: Python) -> PyResult<Option<(Py<PyAny>, Py<PyAny>)>> {
        let next_item = match self.iter.next() {
            Some(result) => result
                .map_err(|e| InvalidDatabaseError::new_err(format!("Iteration error: {}", e)))?,
            None => return Ok(None),
        };

        // Convert IpNetwork to Python ipaddress.IPv4Network or IPv6Network
        let (class, network_str) = match next_item.ip_net {
            ipnetwork::IpNetwork::V4(v4) => (self.ipv4_network_cls.bind(py), v4.to_string()),
            ipnetwork::IpNetwork::V6(v6) => (self.ipv6_network_cls.bind(py), v6.to_string()),
        };
        let network_obj = class.call1((network_str,))?;

        // Record is already materialized as a Python object
        let data_obj = next_item.info.into_py();

        Ok(Some((network_obj.unbind(), data_obj)))
    }
}

enum ReaderWithin {
    Mmap(Within<'static, PyDecodedValue, Mmap>),
    Memory(Within<'static, PyDecodedValue, Vec<u8>>),
}

impl ReaderWithin {
    fn new(
        reader: &Arc<ReaderSource>,
        network: ipnetwork::IpNetwork,
    ) -> Result<Self, maxminddb_crate::MaxMindDbError> {
        match reader.as_ref() {
            ReaderSource::Mmap(inner) => {
                let iter = inner.within::<PyDecodedValue>(network)?;
                // SAFETY: the iterator holds a reference into `inner`. We store an Arc guard
                // alongside it so the reader outlives the transmuted iterator.
                Ok(Self::Mmap(unsafe { transmute(iter) }))
            }
            ReaderSource::Memory(inner) => {
                let iter = inner.within::<PyDecodedValue>(network)?;
                // SAFETY: same as above, the Arc guard in `ReaderIterator` keeps the reader alive.
                Ok(Self::Memory(unsafe { transmute(iter) }))
            }
        }
    }

    fn next(
        &mut self,
    ) -> Option<Result<WithinItem<PyDecodedValue>, maxminddb_crate::MaxMindDbError>> {
        match self {
            ReaderWithin::Mmap(iter) => iter.next(),
            ReaderWithin::Memory(iter) => iter.next(),
        }
    }
}

struct ParsedIp {
    addr: IpAddr,
}

/// Helper function to parse IP address from string or ipaddress objects
#[inline(always)]
fn parse_ip_address(ip_address: &Bound<'_, PyAny>) -> PyResult<ParsedIp> {
    // Fast path: Try string first (most common case)
    if let Ok(py_str) = ip_address.cast::<PyString>() {
        let s = py_str.to_str()?;
        let addr = s.parse().map_err(|_| {
            PyValueError::new_err(format!(
                "'{}' does not appear to be an IPv4 or IPv6 address",
                s
            ))
        })?;
        return Ok(ParsedIp { addr });
    }

    // Slow path: Check if it's an ipaddress.IPv4Address or IPv6Address
    let type_name = ip_address.get_type().name()?;
    if type_name == "IPv4Address" || type_name == "IPv6Address" {
        let addr = ip_address.extract::<IpAddr>()?;
        return Ok(ParsedIp { addr });
    }

    Err(PyTypeError::new_err(
        "argument 1 must be a string or ipaddress object",
    ))
}

/// Helper function to generate IPv6-in-IPv4 error message
#[inline]
fn ipv6_in_ipv4_error(ip: &IpAddr) -> String {
    format!(
        "Error looking up {}. You attempted to look up an IPv6 address in an IPv4-only database",
        ip
    )
}

/// Helper function to open a file with appropriate error handling
fn open_file(path: &str) -> PyResult<File> {
    File::open(Path::new(path)).map_err(|e| match e.kind() {
        std::io::ErrorKind::NotFound => PyFileNotFoundError::new_err(e.to_string()),
        _ => PyIOError::new_err(e.to_string()),
    })
}

/// Helper function to create a Reader from a ReaderSource
fn create_reader(source: ReaderSource) -> Reader {
    let ip_version = source.metadata().ip_version;
    Reader {
        reader: Arc::new(RwLock::new(Some(Arc::new(source)))),
        closed: Arc::new(AtomicBool::new(false)),
        ip_version,
    }
}

/// Open a MaxMind DB using memory-mapped files (MODE_MMAP)
fn open_database_mmap(path: &str) -> PyResult<Reader> {
    // Release GIL during file I/O operation
    let reader = Python::attach(|py| {
        py.detach(|| {
            let file = open_file(path)?;

            // Safety: The mmap is read-only and the file won't be modified
            let mmap = unsafe {
                Mmap::map(&file).map_err(|e| {
                    PyIOError::new_err(format!("Failed to memory-map database: {e}"))
                })?
            };

            MaxMindReader::from_source(mmap).map_err(|_| {
                InvalidDatabaseError::new_err(format!(
                    "Error opening database file ({}). Is this a valid MaxMind DB file?",
                    path
                ))
            })
        })
    })?;

    Ok(create_reader(ReaderSource::Mmap(reader)))
}

/// Open a MaxMind DB by loading entire file into memory (MODE_MEMORY)
fn open_database_memory(path: &str) -> PyResult<Reader> {
    // Release GIL during file I/O operation
    let reader = Python::attach(|py| {
        py.detach(|| {
            let mut file = open_file(path)?;

            let mut buffer = Vec::new();
            file.read_to_end(&mut buffer)
                .map_err(|e| PyIOError::new_err(format!("Failed to read database file: {e}")))?;

            MaxMindReader::from_source(buffer).map_err(|_| {
                InvalidDatabaseError::new_err(format!(
                    "Error opening database file ({}). Is this a valid MaxMind DB file?",
                    path
                ))
            })
        })
    })?;

    Ok(create_reader(ReaderSource::Memory(reader)))
}

/// Open the MaxMind database
#[pyfunction]
#[pyo3(signature = (database, mode=MODE_AUTO))]
fn open_database(database: &Bound<'_, PyAny>, mode: i32) -> PyResult<Reader> {
    Reader::new(database, mode)
}

/// Python module definition
#[pymodule]
fn maxminddb(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
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
