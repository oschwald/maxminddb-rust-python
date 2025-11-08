use maxminddb::Reader as MaxMindReader;
use memmap2::Mmap;
use pyo3::{exceptions::PyValueError, prelude::*, types::PyModule};
use std::fs::File;
use std::net::IpAddr;
use std::path::Path;

/// A Python wrapper around the MaxMind DB reader.
/// Uses memory-mapped files for improved performance.
#[pyclass]
struct Reader {
    reader: MaxMindReader<Mmap>,
}

#[pymethods]
impl Reader {
    #[new]
    fn new(path: &str) -> PyResult<Self> {
        open_database(path)
    }

    #[inline]
    fn get(&self, py: Python, ip: &str) -> PyResult<PyObject> {
        // Release GIL during both parsing and lookup for better concurrency
        let result: Option<serde_json::Value> = py.allow_threads(|| {
            // Parse IP address - using ? here to short-circuit on error
            let ip_addr: IpAddr = ip.parse().ok()?;

            // Perform lookup - using ok() to convert Result to Option
            self.reader.lookup(ip_addr).ok().flatten()
        });

        // Convert result to Python object
        match result {
            Some(data) => {
                // Use pythonize for direct serde to Python conversion
                pythonize::pythonize(py, &data)
                    .map(|obj| obj.unbind())
                    .map_err(|e| PyValueError::new_err(format!("Conversion error: {e}")))
            }
            None => Ok(py.None()),
        }
    }

    /// Batch lookup multiple IP addresses at once to reduce call overhead
    fn get_many(&self, py: Python, ips: Vec<String>) -> PyResult<Vec<PyObject>> {
        // Release GIL during all lookups
        let results: Vec<PyResult<Option<serde_json::Value>>> = py.allow_threads(|| {
            ips.iter()
                .map(|ip| {
                    let ip_addr: IpAddr = ip.parse().map_err(|_| {
                        PyValueError::new_err(format!("Invalid IP address: {ip}"))
                    })?;

                    self.reader
                        .lookup(ip_addr)
                        .map_err(|e| PyValueError::new_err(format!("Lookup error: {e}")))
                })
                .collect()
        });

        // Convert results to Python objects
        results
            .into_iter()
            .map(|result| match result? {
                Some(data) => pythonize::pythonize(py, &data)
                    .map(|obj| obj.unbind())
                    .map_err(|e| PyValueError::new_err(format!("Conversion error: {e}"))),
                None => Ok(py.None()),
            })
            .collect()
    }

    /// Metadata about the database
    fn metadata(&self, py: Python) -> PyResult<PyObject> {
        let metadata = &self.reader.metadata;
        pythonize::pythonize(py, metadata)
            .map(|obj| obj.unbind())
            .map_err(|e| PyValueError::new_err(format!("Metadata conversion error: {e}")))
    }
}

/// Open the MaxMind database using memory-mapped files for better performance
#[pyfunction]
fn open_database(path: &str) -> PyResult<Reader> {
    // Release GIL during file I/O operation
    let reader = Python::with_gil(|py| {
        py.allow_threads(|| {
            // Open file and create memory map
            let file = File::open(Path::new(path)).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyIOError, _>(format!(
                    "Failed to open database file: {e}"
                ))
            })?;

            // Safety: The mmap is read-only and the file won't be modified
            let mmap = unsafe {
                Mmap::map(&file).map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyIOError, _>(format!(
                        "Failed to memory-map database: {e}"
                    ))
                })?
            };

            MaxMindReader::from_source(mmap).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyIOError, _>(format!(
                    "Failed to open database: {e}"
                ))
            })
        })
    })?;
    Ok(Reader { reader })
}

/// Python module definition
#[pymodule]
fn maxminddb_pyo3(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Reader>()?;
    m.add_function(wrap_pyfunction!(open_database, m)?)?;
    Ok(())
}
