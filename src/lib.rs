use ::maxminddb as maxminddb_crate;
use maxminddb_crate::Reader as MaxMindReader;
use memmap2::Mmap;
use pyo3::{
    exceptions::{PyRuntimeError, PyValueError},
    prelude::*,
    types::PyModule,
};
use std::collections::BTreeMap;
use std::fs::File;
use std::net::IpAddr;
use std::path::Path;
use std::str::FromStr;
use std::sync::{atomic::{AtomicBool, Ordering}, Arc, Mutex};

// Define InvalidDatabaseError exception (subclass of RuntimeError)
pyo3::create_exception!(maxminddb_pyo3_exceptions, InvalidDatabaseError, PyRuntimeError, "Invalid MaxMind DB");

// Mode constants matching original maxminddb module
const MODE_AUTO: i32 = 0;
const MODE_MMAP_EXT: i32 = 1;
const MODE_MMAP: i32 = 2;
const MODE_FILE: i32 = 4;
const MODE_MEMORY: i32 = 8;
const MODE_FD: i32 = 16;

/// Metadata about the MaxMind DB database
#[pyclass]
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
        pythonize::pythonize(py, &self.description_dict)
            .map(|obj| obj.unbind())
            .map_err(|e| InvalidDatabaseError::new_err(format!("Metadata conversion error: {e}")))
    }

    #[getter]
    fn languages(&self, py: Python) -> PyResult<PyObject> {
        pythonize::pythonize(py, &self.languages_list)
            .map(|obj| obj.unbind())
            .map_err(|e| InvalidDatabaseError::new_err(format!("Metadata conversion error: {e}")))
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
/// Uses memory-mapped files for improved performance.
#[pyclass]
struct Reader {
    reader: Arc<Mutex<Option<Arc<MaxMindReader<Mmap>>>>>,
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

        // Validate mode (currently only support MODE_AUTO and MODE_MMAP)
        if mode != MODE_AUTO && mode != MODE_MMAP {
            return Err(PyValueError::new_err(format!(
                "Mode {mode} not yet supported, using MODE_MMAP"
            )));
        }

        open_database_internal(&path)
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
        let result: Option<serde_json::Value> = py.allow_threads(|| {
            // Parse IP address
            let ip_addr: IpAddr = ip_str.parse().ok()?;

            // Perform lookup (no lock needed - reader is thread-safe)
            reader.lookup(ip_addr).ok().flatten()
        });

        // Convert result to Python object
        match result {
            Some(data) => {
                // Use pythonize for direct serde to Python conversion
                pythonize::pythonize(py, &data)
                    .map(|obj| obj.unbind())
                    .map_err(|e| InvalidDatabaseError::new_err(format!("Conversion error: {e}")))
            }
            None => Ok(py.None()),
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
        let (result, prefix_len): (Option<serde_json::Value>, usize) = py.allow_threads(|| {
            // Parse IP address
            let ip_addr: IpAddr = match ip_str.parse() {
                Ok(addr) => addr,
                Err(_) => return (None, 0),
            };

            // Perform lookup with prefix length (no lock needed - reader is thread-safe)
            match reader.lookup_prefix::<serde_json::Value>(ip_addr) {
                Ok((Some(data), prefix)) => (Some(data), prefix),
                Ok((None, _)) => (None, 0),
                Err(_) => (None, 0),
            }
        });

        // Convert result to Python object
        let py_obj = match result {
            Some(data) => pythonize::pythonize(py, &data)
                .map(|obj| obj.unbind())
                .map_err(|e| InvalidDatabaseError::new_err(format!("Conversion error: {e}")))?,
            None => py.None(),
        };

        Ok((py_obj, prefix_len))
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
        let results: Vec<PyResult<Option<serde_json::Value>>> = py.allow_threads(|| {
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
                Some(data) => pythonize::pythonize(py, &data)
                    .map(|obj| obj.unbind())
                    .map_err(|e| InvalidDatabaseError::new_err(format!("Conversion error: {e}"))),
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

        let meta = &reader.metadata;

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
    fn __enter__(slf: Py<Self>) -> Py<Self> {
        slf
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
#[pyclass]
struct ReaderIterator {
    items: Vec<(ipnetwork::IpNetwork, serde_json::Value)>,
    current_index: usize,
}

impl ReaderIterator {
    fn new(reader: Arc<MaxMindReader<Mmap>>) -> PyResult<Self> {
        // Collect all items from both IPv4 and IPv6 networks
        let mut items = Vec::new();

        // IPv4: 0.0.0.0/0
        let ipv4_net = ipnetwork::IpNetwork::from_str("0.0.0.0/0")
            .map_err(|e| InvalidDatabaseError::new_err(format!("Failed to create IPv4 network: {e}")))?;

        for result in reader.within(ipv4_net)
            .map_err(|e| InvalidDatabaseError::new_err(format!("Failed to create IPv4 iterator: {e}")))?
        {
            match result {
                Ok(item) => items.push((item.ip_net, item.info)),
                Err(e) => return Err(InvalidDatabaseError::new_err(format!("Iterator error: {e}"))),
            }
        }

        // IPv6: ::/0
        let ipv6_net = ipnetwork::IpNetwork::from_str("::/0")
            .map_err(|e| InvalidDatabaseError::new_err(format!("Failed to create IPv6 network: {e}")))?;

        for result in reader.within(ipv6_net)
            .map_err(|e| InvalidDatabaseError::new_err(format!("Failed to create IPv6 iterator: {e}")))?
        {
            match result {
                Ok(item) => items.push((item.ip_net, item.info)),
                Err(e) => return Err(InvalidDatabaseError::new_err(format!("Iterator error: {e}"))),
            }
        }

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
                let ipaddress = py.import("ipaddress")?;
                ipaddress.call_method1("IPv4Network", (v4.to_string(),))?
            }
            ipnetwork::IpNetwork::V6(v6) => {
                let ipaddress = py.import("ipaddress")?;
                ipaddress.call_method1("IPv6Network", (v6.to_string(),))?
            }
        };

        // Convert data to Python object
        let data_obj = pythonize::pythonize(py, data)
            .map(|obj| obj.unbind())
            .map_err(|e| InvalidDatabaseError::new_err(format!("Conversion error: {e}")))?;

        Ok(Some((network_obj.unbind(), data_obj)))
    }
}

/// Helper function to parse IP address from string or ipaddress objects
fn parse_ip_address(ip_address: &Bound<'_, PyAny>) -> PyResult<String> {
    // Try to extract as string first
    if let Ok(s) = ip_address.extract::<String>() {
        return Ok(s);
    }

    // Try to get the string representation from ipaddress.IPv4Address or IPv6Address
    // These objects have a __str__ method
    if let Ok(s) = ip_address.str() {
        return Ok(s.to_string());
    }

    Err(PyValueError::new_err(
        "ip_address must be a string or ipaddress object",
    ))
}

/// Internal function to open a MaxMind DB from a file path
fn open_database_internal(path: &str) -> PyResult<Reader> {
    // Release GIL during file I/O operation
    let reader = Python::with_gil(|py| {
        py.allow_threads(|| {
            // Open file and create memory map
            let file = File::open(Path::new(path)).map_err(|e| {
                InvalidDatabaseError::new_err(format!("Failed to open database file: {e}"))
            })?;

            // Safety: The mmap is read-only and the file won't be modified
            let mmap = unsafe {
                Mmap::map(&file).map_err(|e| {
                    InvalidDatabaseError::new_err(format!("Failed to memory-map database: {e}"))
                })?
            };

            MaxMindReader::from_source(mmap)
                .map_err(|e| InvalidDatabaseError::new_err(format!("Failed to open database: {e}")))
        })
    })?;

    Ok(Reader {
        reader: Arc::new(Mutex::new(Some(Arc::new(reader)))),
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
