use ::maxminddb as maxminddb_crate;
use maxminddb_crate::Reader as MaxMindReader;
use memmap2::Mmap;
use pyo3::{
    exceptions::{PyFileNotFoundError, PyIOError, PyRuntimeError, PyValueError},
    prelude::*,
    types::{PyDict, PyModule},
};
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use std::collections::BTreeMap;
use std::fs::File;
use std::io::Read as IoRead;
use std::net::IpAddr;
use std::path::Path;
use std::str::FromStr;
use std::sync::{atomic::{AtomicBool, Ordering}, Arc, Mutex};

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
                let list: Result<Vec<_>, _> = arr.iter()
                    .map(|v| v.to_python(py))
                    .collect();
                Ok(list?.to_object(py))
            }
            MaxMindValue::Boolean(b) => Ok(b.to_object(py)),
            MaxMindValue::Bytes(b) => {
                // Create Python bytearray
                let bytearray_class = py.import_bound("builtins")?.getattr("bytearray")?;
                Ok(bytearray_class.call1((b.as_slice(),))?.unbind())
            }
            MaxMindValue::Double(d) => Ok(d.to_object(py)),
            MaxMindValue::Float(f) => Ok(f.to_object(py)),
            MaxMindValue::Int32(i) => Ok(i.to_object(py)),
            MaxMindValue::Map(m) => {
                let dict = PyDict::new_bound(py);
                for (k, v) in m {
                    dict.set_item(k, v.to_python(py)?)?;
                }
                Ok(dict.unbind().into())
            }
            MaxMindValue::String(s) => Ok(s.to_object(py)),
            MaxMindValue::Uint16(u) => Ok(u.to_object(py)),
            MaxMindValue::Uint32(u) => Ok(u.to_object(py)),
            MaxMindValue::Uint64(u) => Ok(u.to_object(py)),
            MaxMindValue::Uint128(u) => {
                // Python int can handle arbitrary precision integers
                Ok(u.to_object(py))
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

    /// Collect all items within a network range
    fn collect_within(&self, network: ipnetwork::IpNetwork) -> Result<Vec<(ipnetwork::IpNetwork, MaxMindValue)>, maxminddb_crate::MaxMindDbError> {
        let mut items = Vec::new();
        match self {
            ReaderSource::Mmap(reader) => {
                for result in reader.within(network)? {
                    let item = result?;
                    items.push((item.ip_net, item.info));
                }
            }
            ReaderSource::Memory(reader) => {
                for result in reader.within(network)? {
                    let item = result?;
                    items.push((item.ip_net, item.info));
                }
            }
        }
        Ok(items)
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
        let dict = PyDict::new_bound(py);
        for (k, v) in &self.description_dict {
            dict.set_item(k, v)?;
        }
        Ok(dict.unbind().into())
    }

    #[getter]
    fn languages(&self, py: Python) -> PyResult<PyObject> {
        // Convert Vec<String> to Python list
        Ok(self.languages_list.to_object(py))
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
    reader: Arc<Mutex<Option<Arc<ReaderSource>>>>,
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
            return Err(PyValueError::new_err("Attempt to read from a closed MaxMind DB"));
        }

        // Parse IP address - support string or ipaddress objects
        let ip_str = parse_ip_address(ip_address)?;

        // Clone the reader Arc for use without holding the lock
        let reader_opt = self.reader.lock().unwrap().clone();
        let reader = reader_opt.ok_or_else(|| PyValueError::new_err("Database is closed"))?;

        // Release GIL during both parsing and lookup for better concurrency
        let result: Result<Option<MaxMindValue>, String> = py.allow_threads(|| {
            // Parse IP address - return error for invalid IP
            let ip_addr: IpAddr = ip_str.parse().map_err(|_| {
                format!("'{}' does not appear to be an IPv4 or IPv6 address", ip_str)
            })?;

            // Perform lookup (no lock needed - reader is thread-safe)
            Ok(reader.lookup(ip_addr).ok().flatten())
        });

        // Handle the result
        match result {
            Ok(Some(data)) => data.to_python(py),
            Ok(None) => Ok(py.None()),
            Err(e) => Err(PyValueError::new_err(e)),
        }
    }

    fn get_with_prefix_len(
        &self,
        py: Python,
        ip_address: &Bound<'_, PyAny>,
    ) -> PyResult<(PyObject, usize)> {
        // Quick check if database is closed
        if self.closed.load(Ordering::Acquire) {
            return Err(PyValueError::new_err("Attempt to read from a closed MaxMind DB"));
        }

        // Parse IP address - support string or ipaddress objects
        let ip_str = parse_ip_address(ip_address)?;

        // Clone the reader Arc for use without holding the lock
        let reader_opt = self.reader.lock().unwrap().clone();
        let reader = reader_opt.ok_or_else(|| PyValueError::new_err("Database is closed"))?;

        // Release GIL during lookup
        let result: Result<(Option<MaxMindValue>, usize), String> = py.allow_threads(|| {
            // Parse IP address - return error for invalid IP
            let ip_addr: IpAddr = ip_str.parse().map_err(|_| {
                format!("'{}' does not appear to be an IPv4 or IPv6 address", ip_str)
            })?;

            // Perform lookup with prefix length (no lock needed - reader is thread-safe)
            match reader.lookup_prefix(ip_addr) {
                Ok((Some(data), prefix)) => Ok((Some(data), prefix)),
                Ok((None, _)) => Ok((None, 0)),
                Err(_) => Ok((None, 0)),
            }
        });

        // Handle the result
        match result {
            Ok((Some(data), prefix_len)) => {
                let py_obj = data.to_python(py)?;
                Ok((py_obj, prefix_len))
            }
            Ok((None, prefix_len)) => Ok((py.None(), prefix_len)),
            Err(e) => Err(PyValueError::new_err(e)),
        }
    }

    /// Batch lookup multiple IP addresses at once to reduce call overhead
    /// This is an extension method not in the original maxminddb module
    fn get_many(&self, py: Python, ips: Vec<String>) -> PyResult<Vec<PyObject>> {
        // Quick check if database is closed
        if self.closed.load(Ordering::Acquire) {
            return Err(PyValueError::new_err("Attempt to read from a closed MaxMind DB"));
        }

        // Clone the reader Arc for use without holding the lock
        let reader_opt = self.reader.lock().unwrap().clone();
        let reader = reader_opt.ok_or_else(|| PyValueError::new_err("Database is closed"))?;

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
            return Err(PyValueError::new_err("Attempt to read from a closed MaxMind DB"));
        }

        // Clone the reader Arc for use
        let reader_opt = self.reader.lock().unwrap().clone();
        let reader = reader_opt.ok_or_else(|| PyValueError::new_err("Database is closed"))?;

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
        *self.reader.lock().unwrap() = None;
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
            return Err(PyValueError::new_err("Attempt to read from a closed MaxMind DB"));
        }

        // Clone the reader Arc
        let reader_opt = slf.reader.lock().unwrap().clone();
        let reader = reader_opt.ok_or_else(|| PyValueError::new_err("Database is closed"))?;

        ReaderIterator::new(reader)
    }
}

/// Iterator for Reader that yields (network, record) tuples
#[pyclass(module = "maxminddb.extension")]
struct ReaderIterator {
    items: Vec<(ipnetwork::IpNetwork, MaxMindValue)>,
    current_index: usize,
}

impl ReaderIterator {
    fn new(reader: Arc<ReaderSource>) -> PyResult<Self> {
        // Collect all items from both IPv4 and IPv6 networks
        let mut items = Vec::new();

        // IPv4: 0.0.0.0/0
        let ipv4_net = ipnetwork::IpNetwork::from_str("0.0.0.0/0")
            .map_err(|e| InvalidDatabaseError::new_err(format!("Failed to create IPv4 network: {e}")))?;

        let mut ipv4_items = reader.collect_within(ipv4_net)
            .map_err(|e| InvalidDatabaseError::new_err(format!("Failed to iterate IPv4: {e}")))?;
        items.append(&mut ipv4_items);

        // IPv6: ::/0
        let ipv6_net = ipnetwork::IpNetwork::from_str("::/0")
            .map_err(|e| InvalidDatabaseError::new_err(format!("Failed to create IPv6 network: {e}")))?;

        let mut ipv6_items = reader.collect_within(ipv6_net)
            .map_err(|e| InvalidDatabaseError::new_err(format!("Failed to iterate IPv6: {e}")))?;
        items.append(&mut ipv6_items);

        Ok(Self {
            items,
            current_index: 0,
        })
    }
}

#[pymethods]
impl ReaderIterator {
    fn __iter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __next__(&mut self, py: Python) -> PyResult<Option<(PyObject, PyObject)>> {
        if self.current_index >= self.items.len() {
            return Ok(None);
        }

        let (network, data) = &self.items[self.current_index];
        self.current_index += 1;

        // Convert IpNetwork to Python ipaddress.IPv4Network or IPv6Network
        let network_obj = match network {
            ipnetwork::IpNetwork::V4(v4) => {
                let ipaddress = py.import_bound("ipaddress")?;
                ipaddress.call_method1("IPv4Network", (v4.to_string(),))?
            }
            ipnetwork::IpNetwork::V6(v6) => {
                let ipaddress = py.import_bound("ipaddress")?;
                ipaddress.call_method1("IPv6Network", (v6.to_string(),))?
            }
        };

        // Convert data to Python object
        let data_obj = data.to_python(py)?;

        Ok(Some((network_obj.unbind(), data_obj)))
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

/// Open a MaxMind DB using memory-mapped files (MODE_MMAP)
fn open_database_mmap(path: &str) -> PyResult<Reader> {
    // Release GIL during file I/O operation
    let reader = Python::with_gil(|py| {
        py.allow_threads(|| {
            // Open file and create memory map
            let file = File::open(Path::new(path)).map_err(|e| {
                // Distinguish between file I/O errors and database errors
                match e.kind() {
                    std::io::ErrorKind::NotFound => {
                        PyFileNotFoundError::new_err(e.to_string())
                    }
                    _ => {
                        PyIOError::new_err(e.to_string())
                    }
                }
            })?;

            // Safety: The mmap is read-only and the file won't be modified
            let mmap = unsafe {
                Mmap::map(&file).map_err(|e| {
                    PyIOError::new_err(format!("Failed to memory-map database: {e}"))
                })?
            };

            MaxMindReader::from_source(mmap)
                .map_err(|e| InvalidDatabaseError::new_err(format!("The MaxMind DB file is invalid or corrupted: {e}")))
        })
    })?;

    Ok(Reader {
        reader: Arc::new(Mutex::new(Some(Arc::new(ReaderSource::Mmap(reader))))),
        closed: Arc::new(AtomicBool::new(false)),
    })
}

/// Open a MaxMind DB by loading entire file into memory (MODE_MEMORY)
fn open_database_memory(path: &str) -> PyResult<Reader> {
    // Release GIL during file I/O operation
    let reader = Python::with_gil(|py| {
        py.allow_threads(|| {
            // Open file and read entire contents into memory
            let mut file = File::open(Path::new(path)).map_err(|e| {
                // Distinguish between file I/O errors and database errors
                match e.kind() {
                    std::io::ErrorKind::NotFound => {
                        PyFileNotFoundError::new_err(e.to_string())
                    }
                    _ => {
                        PyIOError::new_err(e.to_string())
                    }
                }
            })?;

            let mut buffer = Vec::new();
            file.read_to_end(&mut buffer).map_err(|e| {
                PyIOError::new_err(format!("Failed to read database file: {e}"))
            })?;

            MaxMindReader::from_source(buffer)
                .map_err(|e| InvalidDatabaseError::new_err(format!("The MaxMind DB file is invalid or corrupted: {e}")))
        })
    })?;

    Ok(Reader {
        reader: Arc::new(Mutex::new(Some(Arc::new(ReaderSource::Memory(reader))))),
        closed: Arc::new(AtomicBool::new(false)),
    })
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
