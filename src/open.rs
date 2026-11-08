use crate::{InvalidDatabaseError, Reader, ReaderSource};
use arc_swap::ArcSwapOption;
use maxminddb::Reader as MaxMindReader;
use memmap2::Mmap;
use pyo3::{
    exceptions::{PyFileNotFoundError, PyIOError},
    prelude::*,
};
use std::{fmt, fs::File, path::Path};

fn open_file(path: &Path) -> PyResult<File> {
    File::open(path).map_err(|err| match err.kind() {
        std::io::ErrorKind::NotFound => PyFileNotFoundError::new_err(err.to_string()),
        _ => PyIOError::new_err(err.to_string()),
    })
}

fn create_reader(source: ReaderSource) -> Reader {
    let ip_version = source.metadata().ip_version;
    Reader {
        reader: ArcSwapOption::from_pointee(source),
        ip_version,
    }
}

#[inline]
fn reader_from_source<S: AsRef<[u8]>>(
    source_name: impl fmt::Display,
    source: S,
) -> PyResult<MaxMindReader<S>> {
    MaxMindReader::from_source(source).map_err(|_| {
        InvalidDatabaseError::new_err(format!(
            "Error opening database file ({}). Is this a valid MaxMind DB file?",
            source_name
        ))
    })
}

#[inline]
fn load_reader<F, S>(path: &Path, load_source: F) -> PyResult<MaxMindReader<S>>
where
    F: FnOnce(&Path) -> PyResult<S> + Send,
    S: AsRef<[u8]> + Send,
{
    Python::attach(|py| {
        py.detach(|| {
            load_source(path).and_then(|source| reader_from_source(path.display(), source))
        })
    })
}

/// Open a MaxMind DB using memory-mapped files (MODE_MMAP).
pub(crate) fn open_database_mmap(path: &Path) -> PyResult<Reader> {
    let reader = load_reader(path, |path| {
        let file = open_file(path)?;
        // Safety: The mmap is read-only and the file won't be modified.
        unsafe {
            Mmap::map(&file)
                .map_err(|err| PyIOError::new_err(format!("Failed to memory-map database: {err}")))
        }
    })?;

    Ok(create_reader(ReaderSource::Mmap(reader)))
}

/// Open a MaxMind DB by loading the entire file into memory (MODE_MEMORY).
pub(crate) fn open_database_memory(path: &Path) -> PyResult<Reader> {
    let reader = load_reader(path, |path| {
        std::fs::read(path).map_err(|err| match err.kind() {
            std::io::ErrorKind::NotFound => PyFileNotFoundError::new_err(err.to_string()),
            _ => PyIOError::new_err(format!("Failed to read database file: {err}")),
        })
    })?;

    Ok(create_reader(ReaderSource::Memory(reader)))
}

/// Open a MaxMind DB from a file-like object's current position (MODE_FD).
///
/// This mirrors the official package's pure Python reader behavior: the object
/// must provide `read()`. Raw integer OS file descriptors are not accepted.
pub(crate) fn open_database_fd(database: &Bound<'_, PyAny>) -> PyResult<Reader> {
    let filename = fd_database_name(database)?;
    let buffer = database.call_method0("read")?.extract::<Vec<u8>>()?;
    let py = database.py();
    let reader = py.detach(move || reader_from_source(&filename, buffer))?;

    Ok(create_reader(ReaderSource::Memory(reader)))
}

fn fd_database_name(database: &Bound<'_, PyAny>) -> PyResult<String> {
    if let Ok(name) = database.getattr("name") {
        if let Ok(name) = name.extract::<String>() {
            return Ok(name);
        }
    }

    let type_repr = database.get_type().repr()?.extract::<String>()?;
    Ok(format!("<{type_repr}>"))
}
