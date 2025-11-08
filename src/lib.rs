use ::maxminddb as maxminddb_crate;
use maxminddb_crate::{Reader as MaxMindReader, Within, WithinItem};
use memmap2::Mmap;
use pyo3::{
    exceptions::{PyFileNotFoundError, PyIOError, PyOSError, PyRuntimeError, PyValueError},
    prelude::*,
    types::{PyBytes, PyDict, PyList, PyModule},
    conversion::IntoPyObjectExt,
};
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use std::{
    collections::BTreeMap,
    fs::File,
    io::Read as IoRead,
    mem::transmute,
    net::IpAddr,
    path::Path,
    str::FromStr,
    sync::{atomic::{AtomicBool, Ordering}, Arc, RwLock},
};

// Define InvalidDatabaseError exception (subclass of RuntimeError)
pyo3::create_exception!(maxminddb_pyo3_exceptions, InvalidDatabaseError, PyRuntimeError, "Invalid MaxMind DB");

/// Custom value type that represents all possible MaxMind DB data types
/// This allows us to properly handle byte arrays and uint128 which aren't supported by serde_json::Value
#[derive(Debug, Clone)]
enum MaxMindValue {
    Array(Vec<MaxMindValue>),
    Boolean(bool),
    Bytes(Vec<u8>),
    Double(f64),
    Float(f32),
    Int32(i32),
    Map(BTreeMap<String, MaxMindValue>),
    String(String),
    Uint16(u16),
    Uint32(u32),
    Uint64(u64),
    Uint128(u128),
}

impl MaxMindValue {
    /// Convert MaxMindValue to Python object
    fn to_python(&self, py: Python) -> PyResult<PyObject> {
        match self {
            MaxMindValue::Array(arr) => {
                let elements: Vec<_> = arr.iter()
                    .map(|v| v.to_python(py))
                    .collect::<PyResult<_>>()?;
                Ok(PyList::new(py, elements)?.into())
            }
            MaxMindValue::Boolean(b) => Ok(b.into_py_any(py)?),
            MaxMindValue::Bytes(b) => {
                // Return immutable bytes for better compatibility and performance
                Ok(PyBytes::new(py, b).into())
            }
            MaxMindValue::Double(d) => Ok(d.into_py_any(py)?),
            MaxMindValue::Float(f) => Ok(f.into_py_any(py)?),
            MaxMindValue::Int32(i) => Ok(i.into_py_any(py)?),
            MaxMindValue::Map(m) => {
                let dict = PyDict::new(py);
                for (k, v) in m {
                    dict.set_item(k, v.to_python(py)?)?;
                }
                Ok(dict.into())
            }
            MaxMindValue::String(s) => Ok(s.as_str().into_py_any(py)?),
            MaxMindValue::Uint16(u) => Ok(u.into_py_any(py)?),
            MaxMindValue::Uint32(u) => Ok(u.into_py_any(py)?),
            MaxMindValue::Uint64(u) => Ok(u.into_py_any(py)?),
            MaxMindValue::Uint128(u) => {
                // Python int can handle arbitrary precision integers
                Ok(u.into_py_any(py)?)
            }
        }
    }
}

// Implement Deserialize for MaxMindValue to handle MaxMind DB data format
impl<'de> Deserialize<'de> for MaxMindValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        struct MaxMindValueVisitor;

        impl<'de> Visitor<'de> for MaxMindValueVisitor {
            type Value = MaxMindValue;

            fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
                formatter.write_str("any valid MaxMind DB value")
            }

            fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
                Ok(MaxMindValue::Boolean(value))
            }

            fn visit_i32<E>(self, value: i32) -> Result<Self::Value, E> {
                Ok(MaxMindValue::Int32(value))
            }

            fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E>
            where
                E: de::Error,
            {
                // MaxMind DB uses i32 for signed integers
                if value >= i32::MIN as i64 && value <= i32::MAX as i64 {
                    Ok(MaxMindValue::Int32(value as i32))
                } else {
                    Err(E::custom(format!("integer {} out of i32 range", value)))
                }
            }

            fn visit_u16<E>(self, value: u16) -> Result<Self::Value, E> {
                Ok(MaxMindValue::Uint16(value))
            }

            fn visit_u32<E>(self, value: u32) -> Result<Self::Value, E> {
                Ok(MaxMindValue::Uint32(value))
            }

            fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E> {
                Ok(MaxMindValue::Uint64(value))
            }

            fn visit_u128<E>(self, value: u128) -> Result<Self::Value, E> {
                Ok(MaxMindValue::Uint128(value))
            }

            fn visit_f32<E>(self, value: f32) -> Result<Self::Value, E> {
                Ok(MaxMindValue::Float(value))
            }

            fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E> {
                Ok(MaxMindValue::Double(value))
            }

            fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
            where
                E: de::Error,
            {
                Ok(MaxMindValue::String(value.to_owned()))
            }

            fn visit_string<E>(self, value: String) -> Result<Self::Value, E> {
                Ok(MaxMindValue::String(value))
            }

            fn visit_bytes<E>(self, value: &[u8]) -> Result<Self::Value, E>
            where
                E: de::Error,
            {
                Ok(MaxMindValue::Bytes(value.to_owned()))
            }

            fn visit_byte_buf<E>(self, value: Vec<u8>) -> Result<Self::Value, E> {
                Ok(MaxMindValue::Bytes(value))
            }

            fn visit_seq<A>(self, mut seq: A) -> Result<Self::Value, A::Error>
            where
                A: SeqAccess<'de>,
            {
                let mut vec = Vec::new();
                while let Some(elem) = seq.next_element()? {
                    vec.push(elem);
                }
                Ok(MaxMindValue::Array(vec))
            }

            fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
            where
                A: MapAccess<'de>,
            {
                let mut result = BTreeMap::new();
                while let Some((key, value)) = map.next_entry()? {
                    result.insert(key, value);
                }
                Ok(MaxMindValue::Map(result))
            }
        }

        deserializer.deserialize_any(MaxMindValueVisitor)
    }
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
const ERR_BAD_DATA: &str = "The MaxMind DB file's data section contains bad data (unknown data type or corrupt data)";

/// Enum to handle different reader source types
enum ReaderSource {
    Mmap(MaxMindReader<Mmap>),
    Memory(MaxMindReader<Vec<u8>>),
}

impl ReaderSource {
    fn lookup(&self, ip: IpAddr) -> Result<Option<MaxMindValue>, maxminddb_crate::MaxMindDbError> {
        match self {
            ReaderSource::Mmap(reader) => reader.lookup(ip),
            ReaderSource::Memory(reader) => reader.lookup(ip),
        }
    }

    fn lookup_prefix(&self, ip: IpAddr) -> Result<(Option<MaxMindValue>, usize), maxminddb_crate::MaxMindDbError> {
        match self {
            ReaderSource::Mmap(reader) => reader.lookup_prefix(ip),
            ReaderSource::Memory(reader) => reader.lookup_prefix(ip),
        }
    }

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
    fn description(&self, py: Python) -> PyResult<PyObject> {
        // Convert BTreeMap<String, String> to Python dict
        let dict = PyDict::new(py);
        for (k, v) in &self.description_dict {
            dict.set_item(k, v)?;
        }
        Ok(dict.into())
    }

    #[getter]
    fn languages(&self, py: Python) -> PyResult<PyObject> {
        // Convert Vec<String> to Python list
        self.languages_list.clone().into_py_any(py)
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
            MODE_MMAP  // Default to mmap for best performance
        } else {
            mode
        };

        // Validate and open database with appropriate mode
        match actual_mode {
            MODE_MMAP | MODE_MMAP_EXT => open_database_mmap(&path),
            MODE_MEMORY => open_database_memory(&path),
            MODE_FILE => Err(PyValueError::new_err(
                "MODE_FILE not yet supported, use MODE_MMAP, MODE_MMAP_EXT, or MODE_MEMORY"
            )),
            MODE_FD => Err(PyValueError::new_err(
                "MODE_FD not yet supported, use MODE_MMAP, MODE_MMAP_EXT, or MODE_MEMORY"
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
    fn get(&self, py: Python, ip_address: &Bound<'_, PyAny>) -> PyResult<PyObject> {
        // Quick check if database is closed
        if self.closed.load(Ordering::Acquire) {
            return Err(PyValueError::new_err(ERR_CLOSED_DB));
        }

        // Parse IP address - support string or ipaddress objects
        let ip_str = parse_ip_address(ip_address)?;
        let reader = self.get_reader()?;

        // Release GIL during both parsing and lookup for better concurrency
        type LookupResult = Result<Option<MaxMindValue>, String>;
        let result: LookupResult = py.allow_threads(|| {
            // Parse IP address - return error for invalid IP
            let ip_addr: IpAddr = ip_str.parse().map_err(|_| {
                format!("'{}' does not appear to be an IPv4 or IPv6 address", ip_str)
            })?;

            // Check for IPv6 address in IPv4-only database
            let metadata = reader.metadata();
            if metadata.ip_version == 4 && matches!(ip_addr, IpAddr::V6(_)) {
                return Err(ipv6_in_ipv4_error(&ip_str));
            }

            // Perform lookup and propagate errors
            match reader.lookup(ip_addr) {
                Ok(data) => Ok(data),
                Err(e) => {
                    // Convert maxminddb errors to appropriate messages
                    let error_msg = format!("{}", e);
                    if error_msg.contains("Invalid database") {
                        Err(ERR_BAD_DATA.to_string())
                    } else if error_msg.contains("AddressNotFoundError") {
                        Ok(None)
                    } else {
                        Err(format!("Database error: {}", error_msg))
                    }
                }
            }
        });

        // Handle the result
        match result {
            Ok(Some(data)) => data.to_python(py),
            Ok(None) => Ok(py.None()),
            Err(e) => {
                // Determine exception type based on error message
                if e.contains("data section contains bad data") {
                    Err(InvalidDatabaseError::new_err(e))
                } else {
                    Err(PyValueError::new_err(e))
                }
            }
        }
    }

    fn get_with_prefix_len(
        &self,
        py: Python,
        ip_address: &Bound<'_, PyAny>,
    ) -> PyResult<(PyObject, usize)> {
        // Quick check if database is closed
        if self.closed.load(Ordering::Acquire) {
            return Err(PyValueError::new_err(ERR_CLOSED_DB));
        }

        // Parse IP address - support string or ipaddress objects
        let ip_str = parse_ip_address(ip_address)?;
        let reader = self.get_reader()?;

        // Release GIL during lookup
        type PrefixResult = Result<(Option<MaxMindValue>, usize), String>;
        let result: PrefixResult = py.allow_threads(|| {
            // Parse IP address - return error for invalid IP
            let ip_addr: IpAddr = ip_str.parse().map_err(|_| {
                format!("'{}' does not appear to be an IPv4 or IPv6 address", ip_str)
            })?;

            // Check for IPv6 address in IPv4-only database
            let metadata = reader.metadata();
            if metadata.ip_version == 4 && matches!(ip_addr, IpAddr::V6(_)) {
                return Err(ipv6_in_ipv4_error(&ip_str));
            }

            // Perform lookup with prefix length and propagate errors
            match reader.lookup_prefix(ip_addr) {
                Ok((data, prefix)) => Ok((data, prefix)),
                Err(e) => {
                    // Convert maxminddb errors to appropriate messages
                    let error_msg = format!("{}", e);
                    if error_msg.contains("Invalid database") {
                        Err(ERR_BAD_DATA.to_string())
                    } else if error_msg.contains("AddressNotFoundError") {
                        // AddressNotFoundError still provides a prefix length
                        Ok((None, 0))
                    } else if error_msg.contains("IPv6 address in an IPv4-only database") {
                        Err(ipv6_in_ipv4_error(&ip_str))
                    } else {
                        Err(format!("Database error: {}", error_msg))
                    }
                }
            }
        });

        // Handle the result
        match result {
            Ok((Some(data), prefix_len)) => {
                let py_obj = data.to_python(py)?;
                Ok((py_obj, prefix_len))
            }
            Ok((None, prefix_len)) => Ok((py.None(), prefix_len)),
            Err(e) => {
                // Determine exception type based on error message
                if e.contains("data section contains bad data") {
                    Err(InvalidDatabaseError::new_err(e))
                } else {
                    Err(PyValueError::new_err(e))
                }
            }
        }
    }

    /// Batch lookup multiple IP addresses at once to reduce call overhead
    /// This is an extension method not in the original maxminddb module
    fn get_many(&self, py: Python, ips: Vec<String>) -> PyResult<Vec<PyObject>> {
        // Quick check if database is closed
        if self.closed.load(Ordering::Acquire) {
            return Err(PyValueError::new_err(ERR_CLOSED_DB));
        }

        let reader = self.get_reader()?;

        // Release GIL during all lookups
        let results: Vec<PyResult<Option<MaxMindValue>>> = py.allow_threads(|| {
            ips.iter()
                .map(|ip| {
                    let ip_addr: IpAddr = ip.parse().map_err(|_| {
                        PyValueError::new_err(format!("Invalid IP address: {ip}"))
                    })?;

                    // Perform lookup (no lock needed - reader is thread-safe)
                    reader
                        .lookup(ip_addr)
                        .map_err(|e| InvalidDatabaseError::new_err(format!("Lookup error: {e}")))
                })
                .collect()
        });

        // Convert results to Python objects
        results
            .into_iter()
            .map(|result| match result? {
                Some(data) => data.to_python(py),
                None => Ok(py.None()),
            })
            .collect()
    }

    /// Metadata about the database
    fn metadata(&self, _py: Python) -> PyResult<Metadata> {
        // Quick check if database is closed
        if self.closed.load(Ordering::Acquire) {
            return Err(PyOSError::new_err(ERR_CLOSED_DB));
        }

        let reader = self.get_reader().map_err(|_| PyOSError::new_err(ERR_CLOSED_DB))?;

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
            return Err(PyValueError::new_err("Attempt to reopen a closed MaxMind DB"));
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

        let network = ipnetwork::IpNetwork::from_str(network_str)
            .map_err(|e| InvalidDatabaseError::new_err(
                format!("Failed to create {} network: {}", network_type, e)
            ))?;

        let iter = ReaderWithin::new(&reader, network)
            .map_err(|e| InvalidDatabaseError::new_err(
                format!("Failed to iterate {}: {}", network_type, e)
            ))?;

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

    fn __next__(&mut self, py: Python) -> PyResult<Option<(PyObject, PyObject)>> {
        let next_item = match self.iter.next() {
            Some(result) => result.map_err(|e| InvalidDatabaseError::new_err(format!("Iteration error: {}", e)))?,
            None => return Ok(None),
        };

        // Convert IpNetwork to Python ipaddress.IPv4Network or IPv6Network
        let (class, network_str) = match next_item.ip_net {
            ipnetwork::IpNetwork::V4(v4) => (self.ipv4_network_cls.bind(py), v4.to_string()),
            ipnetwork::IpNetwork::V6(v6) => (self.ipv6_network_cls.bind(py), v6.to_string()),
        };
        let network_obj = class.call1((network_str,))?;

        // Convert data to Python object
        let data_obj = next_item.info.to_python(py)?;

        Ok(Some((network_obj.unbind(), data_obj)))
    }
}

enum ReaderWithin {
    Mmap(Within<'static, MaxMindValue, Mmap>),
    Memory(Within<'static, MaxMindValue, Vec<u8>>),
}

impl ReaderWithin {
    fn new(reader: &Arc<ReaderSource>, network: ipnetwork::IpNetwork) -> Result<Self, maxminddb_crate::MaxMindDbError> {
        match reader.as_ref() {
            ReaderSource::Mmap(inner) => {
                let iter = inner.within::<MaxMindValue>(network)?;
                // SAFETY: the iterator holds a reference into `inner`. We store an Arc guard
                // alongside it so the reader outlives the transmuted iterator.
                Ok(Self::Mmap(unsafe { transmute(iter) }))
            }
            ReaderSource::Memory(inner) => {
                let iter = inner.within::<MaxMindValue>(network)?;
                // SAFETY: same as above, the Arc guard in `ReaderIterator` keeps the reader alive.
                Ok(Self::Memory(unsafe { transmute(iter) }))
            }
        }
    }

    fn next(&mut self) -> Option<Result<WithinItem<MaxMindValue>, maxminddb_crate::MaxMindDbError>> {
        match self {
            ReaderWithin::Mmap(iter) => iter.next(),
            ReaderWithin::Memory(iter) => iter.next(),
        }
    }
}

/// Helper function to parse IP address from string or ipaddress objects
fn parse_ip_address(ip_address: &Bound<'_, PyAny>) -> PyResult<String> {
    // Check if it's a string type
    if ip_address.is_instance_of::<pyo3::types::PyString>() {
        return ip_address.extract::<String>();
    }

    // Check if it's an ipaddress object by checking for IPv4Address or IPv6Address type
    // Try to get the string representation from ipaddress.IPv4Address or IPv6Address
    let type_name = ip_address.get_type().name()?;
    if type_name == "IPv4Address" || type_name == "IPv6Address" {
        return Ok(ip_address.str()?.to_string());
    }

    // Not a valid type
    use pyo3::exceptions::PyTypeError;
    Err(PyTypeError::new_err(
        "argument 1 must be a string or ipaddress object",
    ))
}

/// Helper function to generate IPv6-in-IPv4 error message
fn ipv6_in_ipv4_error(ip_str: &str) -> String {
    format!("Error looking up {}. You attempted to look up an IPv6 address in an IPv4-only database", ip_str)
}

/// Helper function to open a file with appropriate error handling
fn open_file(path: &str) -> PyResult<File> {
    File::open(Path::new(path)).map_err(|e| {
        match e.kind() {
            std::io::ErrorKind::NotFound => PyFileNotFoundError::new_err(e.to_string()),
            _ => PyIOError::new_err(e.to_string()),
        }
    })
}

/// Helper function to create a Reader from a ReaderSource
fn create_reader(source: ReaderSource) -> Reader {
    Reader {
        reader: Arc::new(RwLock::new(Some(Arc::new(source)))),
        closed: Arc::new(AtomicBool::new(false)),
    }
}

/// Open a MaxMind DB using memory-mapped files (MODE_MMAP)
fn open_database_mmap(path: &str) -> PyResult<Reader> {
    // Release GIL during file I/O operation
    let reader = Python::with_gil(|py| {
        py.allow_threads(|| {
            let file = open_file(path)?;

            // Safety: The mmap is read-only and the file won't be modified
            let mmap = unsafe {
                Mmap::map(&file).map_err(|e| {
                    PyIOError::new_err(format!("Failed to memory-map database: {e}"))
                })?
            };

            MaxMindReader::from_source(mmap)
                .map_err(|_| InvalidDatabaseError::new_err(format!("Error opening database file ({}). Is this a valid MaxMind DB file?", path)))
        })
    })?;

    Ok(create_reader(ReaderSource::Mmap(reader)))
}

/// Open a MaxMind DB by loading entire file into memory (MODE_MEMORY)
fn open_database_memory(path: &str) -> PyResult<Reader> {
    // Release GIL during file I/O operation
    let reader = Python::with_gil(|py| {
        py.allow_threads(|| {
            let mut file = open_file(path)?;

            let mut buffer = Vec::new();
            file.read_to_end(&mut buffer).map_err(|e| {
                PyIOError::new_err(format!("Failed to read database file: {e}"))
            })?;

            MaxMindReader::from_source(buffer)
                .map_err(|_| InvalidDatabaseError::new_err(format!("Error opening database file ({}). Is this a valid MaxMind DB file?", path)))
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
    m.add("InvalidDatabaseError", _py.get_type::<InvalidDatabaseError>())?;

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
