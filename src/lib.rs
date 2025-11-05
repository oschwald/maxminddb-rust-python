use maxminddb::Reader as MaxMindReader;
use pyo3::{exceptions::PyValueError, prelude::*, types::PyModule};
use std::net::IpAddr;
use std::path::Path;

/// A Python wrapper around the MaxMind DB reader.
#[pyclass]
struct Reader {
    reader: MaxMindReader<Vec<u8>>,
}

#[pymethods]
impl Reader {
    #[new]
    fn new(path: &str) -> PyResult<Self> {
        open_database(path)
    }

    fn get(&self, py: Python, ip: &str) -> PyResult<PyObject> {
        // Parse IP address
        let ip_addr: IpAddr = ip
            .parse()
            .map_err(|_| PyValueError::new_err("Invalid IP address"))?;

        // Release GIL during the lookup operation since it doesn't need Python objects
        let result: PyResult<Option<serde_json::Value>> = py.allow_threads(|| {
            self.reader
                .lookup(ip_addr)
                .map_err(|e| PyValueError::new_err(format!("Lookup error: {}", e)))
        });

        // Convert result to Python object
        match result? {
            Some(data) => {
                // Use pythonize for direct serde to Python conversion
                pythonize::pythonize(py, &data)
                    .map(|obj| obj.unbind())
                    .map_err(|e| PyValueError::new_err(format!("Conversion error: {}", e)))
            }
            None => Ok(py.None()),
        }
    }

    /// Metadata about the database
    fn metadata(&self, py: Python) -> PyResult<PyObject> {
        let metadata = &self.reader.metadata;
        pythonize::pythonize(py, metadata)
            .map(|obj| obj.unbind())
            .map_err(|e| PyValueError::new_err(format!("Metadata conversion error: {}", e)))
    }
}

/// Open the MaxMind database and return a Reader instance
#[pyfunction]
fn open_database(path: &str) -> PyResult<Reader> {
    // Release GIL during file I/O operation
    let reader = Python::with_gil(|py| {
        py.allow_threads(|| {
            MaxMindReader::open_readfile(Path::new(path)).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyIOError, _>(format!(
                    "Failed to open database: {}",
                    e
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
