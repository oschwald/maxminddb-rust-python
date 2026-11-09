use crate::maxminddb_crate::PathElement;
use pyo3::{
    exceptions::PyTypeError,
    prelude::*,
    types::{PyBool, PyInt, PyList, PyListMethods, PyString, PyTuple, PyTupleMethods},
};

pub(crate) enum OwnedPathElement {
    Key(String),
    Index(usize),
    IndexFromEnd(usize),
}

impl OwnedPathElement {
    #[inline]
    pub(crate) fn as_path_element(&self) -> PathElement<'_> {
        match self {
            Self::Key(key) => PathElement::Key(key.as_str()),
            Self::Index(index) => PathElement::Index(*index),
            Self::IndexFromEnd(index) => PathElement::IndexFromEnd(*index),
        }
    }
}

pub(crate) fn path_tuple_matches_owned(
    path: &Bound<'_, PyTuple>,
    cached: &[OwnedPathElement],
) -> bool {
    let slice = path.as_slice();
    slice.len() == cached.len()
        && slice
            .iter()
            .zip(cached)
            .all(|(item, cached_element)| match cached_element {
                OwnedPathElement::Key(cached_key) => item
                    .cast::<PyString>()
                    .ok()
                    .and_then(|value| value.to_str().ok())
                    .is_some_and(|value| value == cached_key),
                OwnedPathElement::Index(cached_index) => {
                    !item.is_instance_of::<PyBool>()
                        && item
                            .cast::<PyInt>()
                            .ok()
                            .and_then(|value| value.extract::<usize>().ok())
                            .is_some_and(|index| index == *cached_index)
                }
                OwnedPathElement::IndexFromEnd(cached_index) => {
                    !item.is_instance_of::<PyBool>()
                        && item
                            .cast::<PyInt>()
                            .ok()
                            .and_then(|value| value.extract::<isize>().ok())
                            .map(signed_index_to_owned_path_element)
                            .is_some_and(|element| match element {
                                OwnedPathElement::IndexFromEnd(index) => index == *cached_index,
                                OwnedPathElement::Key(_) | OwnedPathElement::Index(_) => false,
                            })
                }
            })
}

const ERR_PATH_SEQUENCE: &str = "Path must be a sequence (list or tuple)";
const ERR_PATH_ELEMENT: &str = "Path elements must be strings or integers";

pub(crate) fn parse_path(path: &Bound<'_, PyAny>) -> PyResult<Vec<OwnedPathElement>> {
    if path.is_instance_of::<PyString>() {
        return Err(PyTypeError::new_err(ERR_PATH_SEQUENCE));
    }

    // The fast paths read list and tuple storage directly. Exact types only:
    // a subclass may override `__iter__`, so it takes the iterator path below.
    if let Ok(tuple) = path.cast_exact::<PyTuple>() {
        let slice = tuple.as_slice();
        let mut owned_path = Vec::with_capacity(slice.len());
        for item in slice {
            owned_path.push(parse_path_element(item)?);
        }
        return Ok(owned_path);
    }

    if let Ok(list) = path.cast_exact::<PyList>() {
        let mut owned_path = Vec::with_capacity(list.len());
        for item in list.iter() {
            owned_path.push(parse_path_element(&item)?);
        }
        return Ok(owned_path);
    }

    let iterator = path
        .try_iter()
        .map_err(|_| PyTypeError::new_err(ERR_PATH_SEQUENCE))?;
    let mut owned_path = Vec::new();
    for item in iterator {
        owned_path.push(parse_path_element(&item?)?);
    }

    Ok(owned_path)
}

#[inline]
fn parse_path_element(item: &Bound<'_, PyAny>) -> PyResult<OwnedPathElement> {
    if item.is_instance_of::<PyBool>() {
        return Err(PyTypeError::new_err(ERR_PATH_ELEMENT));
    }
    if let Ok(py_str) = item.cast::<PyString>() {
        // A str that is not valid UTF-8 (for example a lone surrogate) is not a
        // usable key. Raise the documented TypeError, chained to the encode error.
        let key = py_str.to_str().map_err(|err| {
            let type_err = PyTypeError::new_err(ERR_PATH_ELEMENT);
            type_err.set_cause(item.py(), Some(err));
            type_err
        })?;
        return Ok(OwnedPathElement::Key(key.to_owned()));
    }
    if item.cast::<PyInt>().is_ok() {
        if let Ok(i) = item.extract::<isize>() {
            return Ok(signed_index_to_owned_path_element(i));
        }
        if let Ok(i) = item.extract::<usize>() {
            return Ok(OwnedPathElement::Index(i));
        }
    }
    Err(PyTypeError::new_err(ERR_PATH_ELEMENT))
}

#[inline]
fn signed_index_to_owned_path_element(n: isize) -> OwnedPathElement {
    if n >= 0 {
        OwnedPathElement::Index(n as usize)
    } else {
        let index = n
            .checked_neg()
            .and_then(|n| n.checked_sub(1))
            .map(|n| n as usize)
            .unwrap_or(usize::MAX);
        OwnedPathElement::IndexFromEnd(index)
    }
}

pub(crate) fn path_elements_from_owned_path(path: &[OwnedPathElement]) -> Vec<PathElement<'_>> {
    path.iter().map(OwnedPathElement::as_path_element).collect()
}
