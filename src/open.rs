use crate::{InvalidDatabaseError, OpenMode, Reader, ReaderSource};
use arc_swap::ArcSwapOption;
use maxminddb::{MaxMindDbError, Reader as MaxMindReader};
use memmap2::Mmap;
use pyo3::{
    buffer::PyBuffer,
    exceptions::{PyOSError, PyTypeError, PyUnicodeDecodeError},
    prelude::*,
    types::{PyBool, PyByteArray, PyBytes, PyInt, PyString},
};
use std::{
    fmt,
    fs::File,
    io::{self, Read},
    path::Path,
};

/// Convert an I/O error from opening `path` into a Python `OSError`.
///
/// Build the exception from `(errno, strerror, filename)`, as `open()` does,
/// so Python picks the subclass (`FileNotFoundError`, `PermissionError`,
/// `IsADirectoryError`, ...) and sets `errno` and `filename`. On Windows the
/// raw code is a Windows error code, so pass it as `winerror` and let Python
/// map it to `errno`.
fn os_error(err: &io::Error, path: &Path) -> PyErr {
    os_error_with_context(err, path, None)
}

/// Like [`os_error`], with `context` put in front of the OS's description.
fn os_error_with_context(err: &io::Error, path: &Path, context: Option<&str>) -> PyErr {
    let Some(code) = err.raw_os_error() else {
        return PyErr::from(io::Error::new(
            err.kind(),
            format!("{err}: {}", path.display()),
        ));
    };

    // Rust's message for an OS error ends with " (os error N)". Python shows
    // the code itself, so keep only the OS's description.
    let message = err.to_string();
    let strerror = message
        .strip_suffix(&format!(" (os error {code})"))
        .map_or_else(|| message.clone(), str::to_owned);
    let strerror = match context {
        Some(context) => format!("{context}: {strerror}"),
        None => strerror,
    };
    let filename = path.as_os_str().to_os_string();

    #[cfg(windows)]
    {
        PyOSError::new_err((code, strerror, filename, code))
    }
    #[cfg(not(windows))]
    {
        PyOSError::new_err((code, strerror, filename))
    }
}

fn open_file(path: &Path) -> PyResult<File> {
    let file = File::open(path).map_err(|err| os_error(&err, path))?;
    // Opening a directory succeeds on Unix, and mapping it then fails with an
    // unrelated error (ENODEV on Linux). Read from it instead, so the OS
    // reports the error that `open()` in Python would (EISDIR).
    if file.metadata().is_ok_and(|metadata| metadata.is_dir()) {
        if let Err(err) = (&file).read(&mut [0; 1]) {
            return Err(os_error(&err, path));
        }
    }
    Ok(file)
}

fn create_reader(source: ReaderSource) -> Reader {
    let ip_version = source.metadata().ip_version;
    Reader {
        reader: ArcSwapOption::from_pointee(source),
        ip_version,
    }
}

/// Raise the official reader's error for a file that is not a valid
/// MaxMind DB, with the Rust crate's own message as its `__cause__`.
#[cold]
fn open_error(py: Python, source_name: impl fmt::Display, err: MaxMindDbError) -> PyErr {
    let py_err = InvalidDatabaseError::new_err(format!(
        "Error opening database file ({}). Is this a valid MaxMind DB file?",
        source_name
    ));
    py_err.set_cause(py, Some(InvalidDatabaseError::new_err(err.to_string())));
    py_err
}

#[inline]
fn load_reader<F, S>(path: &Path, load_source: F) -> PyResult<MaxMindReader<S>>
where
    F: FnOnce(&Path) -> PyResult<S> + Send,
    S: AsRef<[u8]> + Send,
{
    Python::attach(|py| {
        py.detach(|| load_source(path).map(MaxMindReader::from_source))?
            .map_err(|err| open_error(py, path.display(), err))
    })
}

/// Open a MaxMind DB using memory-mapped files (MODE_MMAP).
pub(crate) fn open_database_mmap(path: &Path) -> PyResult<Reader> {
    let reader = load_reader(path, |path| {
        let file = open_file(path)?;
        // Safety: The mmap is read-only and the file won't be modified.
        unsafe { Mmap::map(&file).map_err(|err| os_error(&err, path)) }
    })?;

    Ok(create_reader(ReaderSource::Mmap(reader)))
}

/// Open a MaxMind DB by loading the entire file into memory (MODE_MEMORY).
pub(crate) fn open_database_memory(path: &Path) -> PyResult<Reader> {
    let reader = load_reader(path, |path| {
        std::fs::read(path).map_err(|err| os_error(&err, path))
    })?;

    Ok(create_reader(ReaderSource::Memory(reader)))
}

/// Open a MaxMind DB from a file-like object's current position (MODE_FD, or
/// MODE_AUTO with a file object).
///
/// This mirrors the official package's pure Python reader behavior: the object
/// must provide `read()`, which must return bytes. Raw integer OS file
/// descriptors are not accepted.
pub(crate) fn open_database_fd(database: &Bound<'_, PyAny>) -> PyResult<Reader> {
    let filename = fd_database_name(database)?;
    let py = database.py();
    let contents = database
        .call_method0(pyo3::intern!(py, "read"))
        .map_err(|err| {
            if err.is_instance_of::<PyUnicodeDecodeError>(py) {
                let type_err = PyTypeError::new_err(
                    "The database file object is a text file. Open it in binary mode.",
                );
                type_err.set_cause(py, Some(err));
                type_err
            } else {
                err
            }
        })?;
    let buffer = if let Ok(bytes) = contents.cast::<PyBytes>() {
        bytes.as_bytes().to_vec()
    } else if let Ok(bytes) = contents.cast::<PyByteArray>() {
        bytes.to_vec()
    } else if contents.is_instance_of::<PyString>() {
        return Err(PyTypeError::new_err(format!(
            "The database file object returned {}, not bytes. Open it in binary mode.",
            type_name(&contents)?
        )));
    } else {
        // Other buffers, such as an mmap, as the official package accepts.
        // Copy them through the buffer protocol where we can, which avoids
        // extracting each byte as a Python int.
        if let Ok(buffer) = PyBuffer::<u8>::get(&contents) {
            buffer.to_vec(py)?
        } else {
            match contents.extract::<Vec<u8>>() {
                Ok(buffer) => buffer,
                Err(err) if err.is_instance_of::<PyTypeError>(py) => {
                    let type_err = PyTypeError::new_err(format!(
                        "The database file object returned {}, not bytes.",
                        type_name(&contents)?
                    ));
                    type_err.set_cause(py, Some(err));
                    return Err(type_err);
                }
                Err(err) => return Err(err),
            }
        }
    };
    let reader = py
        .detach(move || MaxMindReader::from_source(buffer))
        .map_err(|err| open_error(py, &filename, err))?;

    Ok(create_reader(ReaderSource::Memory(reader)))
}

/// Whether the database argument has a callable `read()`, as a file object has.
pub(crate) fn has_callable_read(database: &Bound<'_, PyAny>) -> PyResult<bool> {
    match database.getattr(pyo3::intern!(database.py(), "read")) {
        Ok(read) => Ok(read.is_callable()),
        Err(err) if err.is_instance_of::<pyo3::exceptions::PyAttributeError>(database.py()) => {
            Ok(false)
        }
        Err(err) => Err(err),
    }
}

/// The TypeError for a database argument that does not suit the mode. The
/// messages follow the official package's pure Python reader.
pub(crate) fn unsupported_database_type(database: &Bound<'_, PyAny>, mode: OpenMode) -> PyErr {
    let hint = match mode {
        OpenMode::Auto => "Pass a path or a binary file object.",
        OpenMode::Fd => {
            "MODE_FD takes a binary file object. Use MODE_MMAP, MODE_FILE or MODE_MEMORY for a path."
        }
        _ => "Pass a path. Use MODE_FD for a file object.",
    };
    // An int, but not a bool, could be a file descriptor, which the official
    // package accepts in some modes.
    let descriptor_note =
        if database.is_instance_of::<PyInt>() && !database.is_instance_of::<PyBool>() {
            " File descriptors are not supported."
        } else {
            ""
        };
    match type_name(database) {
        Ok(name) => PyTypeError::new_err(format!(
            "Unsupported database type ({name}). {hint}{descriptor_note}"
        )),
        Err(err) => err,
    }
}

/// The type's name, with its module unless it is a builtin, so that a
/// zipfile.Path is not just "Path".
fn type_name(value: &Bound<'_, PyAny>) -> PyResult<String> {
    let value_type = value.get_type();
    let qualname = value_type.qualname()?.to_string();
    let module = value_type.module()?.to_string();
    if module == "builtins" {
        Ok(qualname)
    } else {
        Ok(format!("{module}.{qualname}"))
    }
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
