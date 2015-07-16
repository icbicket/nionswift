"""
    Provide symbolic math services.

    The goal is to provide a module (namespace) where users can be provided with variables representing
    data items (directly or indirectly via reference to workspace panels).

    DataNodes represent data items, operations, numpy arrays, and constants.
"""

# futures
from __future__ import absolute_import

# standard libraries
import copy
import datetime
import logging
import numbers
import operator
import uuid

# third party libraries
import numpy
import scipy
import scipy.fftpack
import scipy.ndimage
import scipy.ndimage.filters
import scipy.ndimage.fourier
import scipy.signal

# local libraries
from nion.swift.model import Calibration
from nion.swift.model import DataAndMetadata
from nion.swift.model import Image
from nion.ui import Geometry
from nion.ui import Observable


def arange(data):
    return numpy.amax(data) - numpy.amin(data)

def take_slice(data, key):
    return data[key].copy()


def function_fft(data_and_metadata):
    data_shape = data_and_metadata.data_shape
    data_dtype = data_and_metadata.data_dtype

    def calculate_data():
        data = data_and_metadata.data
        if data is None or not Image.is_data_valid(data):
            return None
        # scaling: numpy.sqrt(numpy.mean(numpy.absolute(data_copy)**2)) == numpy.sqrt(numpy.mean(numpy.absolute(data_copy_fft)**2))
        # see https://gist.github.com/endolith/1257010
        if Image.is_data_1d(data):
            scaling = 1.0 / numpy.sqrt(data_shape[0])
            return scipy.fftpack.fftshift(scipy.fftpack.fft(data) * scaling)
        elif Image.is_data_2d(data):
            data_copy = data.copy()  # let other threads use data while we're processing
            scaling = 1.0 / numpy.sqrt(data_shape[1] * data_shape[0])
            return scipy.fftpack.fftshift(scipy.fftpack.fft2(data_copy) * scaling)
        else:
            raise NotImplementedError()

    src_dimensional_calibrations = data_and_metadata.dimensional_calibrations

    if not Image.is_shape_and_dtype_valid(data_shape, data_dtype) or src_dimensional_calibrations is None:
        return None

    assert len(src_dimensional_calibrations) == len(
        Image.dimensional_shape_from_shape_and_dtype(data_shape, data_dtype))

    data_shape_and_dtype = data_shape, numpy.dtype(numpy.complex128)

    dimensional_calibrations = [Calibration.Calibration(0.0, 1.0 / (dimensional_calibration.scale * data_shape_n),
                                                        "1/" + dimensional_calibration.units) for
        dimensional_calibration, data_shape_n in zip(src_dimensional_calibrations, data_shape)]

    return DataAndMetadata.DataAndMetadata(calculate_data, data_shape_and_dtype, Calibration.Calibration(),
                                           dimensional_calibrations, dict(), datetime.datetime.utcnow())


def function_ifft(data_and_metadata):
    data_shape = data_and_metadata.data_shape
    data_dtype = data_and_metadata.data_dtype

    def calculate_data():
        data = data_and_metadata.data
        if data is None or not Image.is_data_valid(data):
            return None
        # scaling: numpy.sqrt(numpy.mean(numpy.absolute(data_copy)**2)) == numpy.sqrt(numpy.mean(numpy.absolute(data_copy_fft)**2))
        # see https://gist.github.com/endolith/1257010
        if Image.is_data_1d(data):
            scaling = numpy.sqrt(data_shape[0])
            return scipy.fftpack.fftshift(scipy.fftpack.ifft(data) * scaling)
        elif Image.is_data_2d(data):
            data_copy = data.copy()  # let other threads use data while we're processing
            scaling = numpy.sqrt(data_shape[1] * data_shape[0])
            return scipy.fftpack.ifft2(scipy.fftpack.ifftshift(data_copy) * scaling)
        else:
            raise NotImplementedError()

    src_dimensional_calibrations = data_and_metadata.dimensional_calibrations

    if not Image.is_shape_and_dtype_valid(data_shape, data_dtype) or src_dimensional_calibrations is None:
        return None

    assert len(src_dimensional_calibrations) == len(
        Image.dimensional_shape_from_shape_and_dtype(data_shape, data_dtype))

    data_shape_and_dtype = data_shape, data_dtype

    dimensional_calibrations = [Calibration.Calibration(0.0, 1.0 / (dimensional_calibration.scale * data_shape_n),
                                                        "1/" + dimensional_calibration.units) for
        dimensional_calibration, data_shape_n in zip(src_dimensional_calibrations, data_shape)]

    return DataAndMetadata.DataAndMetadata(calculate_data, data_shape_and_dtype, Calibration.Calibration(),
                                           dimensional_calibrations, dict(), datetime.datetime.utcnow())


def function_autocorrelate(data_and_metadata):
    def calculate_data():
        data = data_and_metadata.data
        if data is None or not Image.is_data_valid(data):
            return None
        if Image.is_data_2d(data):
            data_copy = data.copy()  # let other threads use data while we're processing
            data_std = data_copy.std(dtype=numpy.float64)
            if data_std != 0.0:
                data_norm = (data_copy - data_copy.mean(dtype=numpy.float64)) / data_std
            else:
                data_norm = data_copy
            scaling = 1.0 / (data_norm.shape[0] * data_norm.shape[1])
            data_norm = numpy.fft.rfft2(data_norm)
            return numpy.fft.fftshift(numpy.fft.irfft2(data_norm * numpy.conj(data_norm))) * scaling
            # this gives different results. why? because for some reason scipy pads out to 1023 and does calculation.
            # see https://github.com/scipy/scipy/blob/master/scipy/signal/signaltools.py
            # return scipy.signal.fftconvolve(data_copy, numpy.conj(data_copy), mode='same')
        return None

    if data_and_metadata is None:
        return None

    dimensional_calibrations = [Calibration.Calibration() for _ in data_and_metadata.data_shape]

    return DataAndMetadata.DataAndMetadata(calculate_data, data_and_metadata.data_shape_and_dtype,
                                           Calibration.Calibration(), dimensional_calibrations, dict(),
                                           datetime.datetime.utcnow())


def function_crosscorrelate(*args):
    if len(args) != 2:
        return None

    data_and_metadata1, data_and_metadata2 = args[0], args[1]

    def calculate_data():
        data1 = data_and_metadata1.data
        data2 = data_and_metadata2.data
        if data1 is None or data2 is None:
            return None
        if Image.is_data_2d(data1) and Image.is_data_2d(data2):
            data_std1 = data1.std(dtype=numpy.float64)
            if data_std1 != 0.0:
                norm1 = (data1 - data1.mean(dtype=numpy.float64)) / data_std1
            else:
                norm1 = data1
            data_std2 = data2.std(dtype=numpy.float64)
            if data_std2 != 0.0:
                norm2 = (data2 - data2.mean(dtype=numpy.float64)) / data_std2
            else:
                norm2 = data2
            scaling = 1.0 / (norm1.shape[0] * norm1.shape[1])
            return numpy.fft.fftshift(numpy.fft.irfft2(numpy.fft.rfft2(norm1) * numpy.conj(numpy.fft.rfft2(norm2)))) * scaling
            # this gives different results. why? because for some reason scipy pads out to 1023 and does calculation.
            # see https://github.com/scipy/scipy/blob/master/scipy/signal/signaltools.py
            # return scipy.signal.fftconvolve(data1.copy(), numpy.conj(data2.copy()), mode='same')
        return None

    if data_and_metadata1 is None or data_and_metadata2 is None:
        return None

    dimensional_calibrations = [Calibration.Calibration() for _ in data_and_metadata1.data_shape]

    return DataAndMetadata.DataAndMetadata(calculate_data, data_and_metadata1.data_shape_and_dtype,
                                           Calibration.Calibration(), dimensional_calibrations, dict(),
                                           datetime.datetime.utcnow())


def function_sobel(data_and_metadata):
    def calculate_data():
        data = data_and_metadata.data
        if not Image.is_data_valid(data):
            return None
        if Image.is_shape_and_dtype_rgb(data.shape, data.dtype):
            rgb = numpy.empty(data.shape[:-1] + (3,), numpy.uint8)
            rgb[..., 0] = scipy.ndimage.sobel(data[..., 0])
            rgb[..., 1] = scipy.ndimage.sobel(data[..., 1])
            rgb[..., 2] = scipy.ndimage.sobel(data[..., 2])
            return rgb
        elif Image.is_shape_and_dtype_rgba(data.shape, data.dtype):
            rgba = numpy.empty(data.shape[:-1] + (4,), numpy.uint8)
            rgba[..., 0] = scipy.ndimage.sobel(data[..., 0])
            rgba[..., 1] = scipy.ndimage.sobel(data[..., 1])
            rgba[..., 2] = scipy.ndimage.sobel(data[..., 2])
            rgba[..., 3] = data[..., 3]
            return rgba
        else:
            return scipy.ndimage.sobel(data)

    return DataAndMetadata.DataAndMetadata(calculate_data, data_and_metadata.data_shape_and_dtype,
                                           data_and_metadata.intensity_calibration,
                                           data_and_metadata.dimensional_calibrations, data_and_metadata.metadata,
                                           datetime.datetime.utcnow())


def function_laplace(data_and_metadata):
    def calculate_data():
        data = data_and_metadata.data
        if not Image.is_data_valid(data):
            return None
        if Image.is_shape_and_dtype_rgb(data.shape, data.dtype):
            rgb = numpy.empty(data.shape[:-1] + (3,), numpy.uint8)
            rgb[..., 0] = scipy.ndimage.laplace(data[..., 0])
            rgb[..., 1] = scipy.ndimage.laplace(data[..., 1])
            rgb[..., 2] = scipy.ndimage.laplace(data[..., 2])
            return rgb
        elif Image.is_shape_and_dtype_rgba(data.shape, data.dtype):
            rgba = numpy.empty(data.shape[:-1] + (4,), numpy.uint8)
            rgba[..., 0] = scipy.ndimage.laplace(data[..., 0])
            rgba[..., 1] = scipy.ndimage.laplace(data[..., 1])
            rgba[..., 2] = scipy.ndimage.laplace(data[..., 2])
            rgba[..., 3] = data[..., 3]
            return rgba
        else:
            return scipy.ndimage.laplace(data)

    return DataAndMetadata.DataAndMetadata(calculate_data, data_and_metadata.data_shape_and_dtype,
                                           data_and_metadata.intensity_calibration,
                                           data_and_metadata.dimensional_calibrations, data_and_metadata.metadata,
                                           datetime.datetime.utcnow())


def function_gaussian_blur(data_and_metadata, sigma):
    sigma = float(sigma)

    def calculate_data():
        data = data_and_metadata.data
        if not Image.is_data_valid(data):
            return None
        return scipy.ndimage.gaussian_filter(data, sigma=sigma)

    return DataAndMetadata.DataAndMetadata(calculate_data, data_and_metadata.data_shape_and_dtype,
                                           data_and_metadata.intensity_calibration,
                                           data_and_metadata.dimensional_calibrations, data_and_metadata.metadata,
                                           datetime.datetime.utcnow())


def function_median_filter(data_and_metadata, size):
    size = max(min(int(size), 999), 1)

    def calculate_data():
        data = data_and_metadata.data
        if not Image.is_data_valid(data):
            return None
        if Image.is_shape_and_dtype_rgb(data.shape, data.dtype):
            rgb = numpy.empty(data.shape[:-1] + (3,), numpy.uint8)
            rgb[..., 0] = scipy.ndimage.median_filter(data[..., 0], size=size)
            rgb[..., 1] = scipy.ndimage.median_filter(data[..., 1], size=size)
            rgb[..., 2] = scipy.ndimage.median_filter(data[..., 2], size=size)
            return rgb
        elif Image.is_shape_and_dtype_rgba(data.shape, data.dtype):
            rgba = numpy.empty(data.shape[:-1] + (4,), numpy.uint8)
            rgba[..., 0] = scipy.ndimage.median_filter(data[..., 0], size=size)
            rgba[..., 1] = scipy.ndimage.median_filter(data[..., 1], size=size)
            rgba[..., 2] = scipy.ndimage.median_filter(data[..., 2], size=size)
            rgba[..., 3] = data[..., 3]
            return rgba
        else:
            return scipy.ndimage.median_filter(data, size=size)

    return DataAndMetadata.DataAndMetadata(calculate_data, data_and_metadata.data_shape_and_dtype,
                                           data_and_metadata.intensity_calibration,
                                           data_and_metadata.dimensional_calibrations, data_and_metadata.metadata,
                                           datetime.datetime.utcnow())


def function_uniform_filter(data_and_metadata, size):
    size = max(min(int(size), 999), 1)

    def calculate_data():
        data = data_and_metadata.data
        if not Image.is_data_valid(data):
            return None
        if Image.is_shape_and_dtype_rgb(data.shape, data.dtype):
            rgb = numpy.empty(data.shape[:-1] + (3,), numpy.uint8)
            rgb[..., 0] = scipy.ndimage.uniform_filter(data[..., 0], size=size)
            rgb[..., 1] = scipy.ndimage.uniform_filter(data[..., 1], size=size)
            rgb[..., 2] = scipy.ndimage.uniform_filter(data[..., 2], size=size)
            return rgb
        elif Image.is_shape_and_dtype_rgba(data.shape, data.dtype):
            rgba = numpy.empty(data.shape[:-1] + (4,), numpy.uint8)
            rgba[..., 0] = scipy.ndimage.uniform_filter(data[..., 0], size=size)
            rgba[..., 1] = scipy.ndimage.uniform_filter(data[..., 1], size=size)
            rgba[..., 2] = scipy.ndimage.uniform_filter(data[..., 2], size=size)
            rgba[..., 3] = data[..., 3]
            return rgba
        else:
            return scipy.ndimage.uniform_filter(data, size=size)

    return DataAndMetadata.DataAndMetadata(calculate_data, data_and_metadata.data_shape_and_dtype,
                                           data_and_metadata.intensity_calibration,
                                           data_and_metadata.dimensional_calibrations, data_and_metadata.metadata,
                                           datetime.datetime.utcnow())


def function_transpose_flip(data_and_metadata, transpose=False, flip_v=False, flip_h=False):
    def calculate_data():
        data = data_and_metadata.data
        data_id = id(data)
        if not Image.is_data_valid(data):
            return None
        if transpose:
            if Image.is_shape_and_dtype_rgb_type(data.shape, data.dtype):
                data = numpy.transpose(data, [1, 0, 2])
            else:
                data = numpy.transpose(data, [1, 0])
        if flip_h:
            data = numpy.fliplr(data)
        if flip_v:
            data = numpy.flipud(data)
        if id(data) == data_id:  # ensure real data, not a view
            data = data.copy()
        return data

    data_shape = data_and_metadata.data_shape
    data_dtype = data_and_metadata.data_dtype

    if not Image.is_shape_and_dtype_valid(data_shape, data_dtype):
        return None

    if transpose:
        dimensional_calibrations = list(reversed(data_and_metadata.dimensional_calibrations))
    else:
        dimensional_calibrations = data_and_metadata.dimensional_calibrations

    if transpose:
        if Image.is_shape_and_dtype_rgb_type(data_shape, data_dtype):
            data_shape = list(reversed(data_shape[0:2])) + [data_shape[-1], ]
        else:
            data_shape = list(reversed(data_shape))

    return DataAndMetadata.DataAndMetadata(calculate_data, (data_shape, data_dtype),
                                           data_and_metadata.intensity_calibration, dimensional_calibrations,
                                           data_and_metadata.metadata, datetime.datetime.utcnow())


def function_crop(data_and_metadata, bounds):
    data_shape = data_and_metadata.data_shape
    data_dtype = data_and_metadata.data_dtype

    def calculate_data():
        data = data_and_metadata.data
        if not Image.is_data_valid(data):
            return None
        data_shape = data_and_metadata.data_shape
        bounds_int = ((int(data_shape[0] * bounds[0][0]), int(data_shape[1] * bounds[0][1])),
            (int(data_shape[0] * bounds[1][0]), int(data_shape[1] * bounds[1][1])))
        return data[bounds_int[0][0]:bounds_int[0][0] + bounds_int[1][0],
            bounds_int[0][1]:bounds_int[0][1] + bounds_int[1][1]].copy()

    dimensional_calibrations = data_and_metadata.dimensional_calibrations

    if not Image.is_shape_and_dtype_valid(data_shape, data_dtype) or dimensional_calibrations is None:
        return None

    bounds_int = ((int(data_shape[0] * bounds[0][0]), int(data_shape[1] * bounds[0][1])),
        (int(data_shape[0] * bounds[1][0]), int(data_shape[1] * bounds[1][1])))

    if Image.is_shape_and_dtype_rgb_type(data_shape, data_dtype):
        data_shape_and_dtype = bounds_int[1] + (data_shape[-1], ), data_dtype
    else:
        data_shape_and_dtype = bounds_int[1], data_dtype

    cropped_dimensional_calibrations = list()
    for index, dimensional_calibration in enumerate(dimensional_calibrations):
        cropped_calibration = Calibration.Calibration(
            dimensional_calibration.offset + data_shape[index] * bounds[0][index] * dimensional_calibration.scale,
            dimensional_calibration.scale, dimensional_calibration.units)
        cropped_dimensional_calibrations.append(cropped_calibration)

    return DataAndMetadata.DataAndMetadata(calculate_data, data_shape_and_dtype,
                                           data_and_metadata.intensity_calibration, cropped_dimensional_calibrations,
                                           data_and_metadata.metadata, datetime.datetime.utcnow())


def function_slice_sum(data_and_metadata, slice_center, slice_width):
    slice_center = int(slice_center)
    slice_width = int(slice_width)

    data_shape = data_and_metadata.data_shape
    data_dtype = data_and_metadata.data_dtype

    def calculate_data():
        data = data_and_metadata.data
        if not Image.is_data_valid(data):
            return None
        shape = data.shape
        slice_start = slice_center + 1 - slice_width
        slice_start = max(slice_start, 0)
        slice_end = slice_start + slice_width
        slice_end = min(shape[0], slice_end)
        return numpy.sum(data[slice_start:slice_end,:], 0)

    dimensional_calibrations = data_and_metadata.dimensional_calibrations

    if not Image.is_shape_and_dtype_valid(data_shape, data_dtype) or dimensional_calibrations is None:
        return None

    data_shape_and_dtype = data_shape[1:], data_dtype

    dimensional_calibrations = dimensional_calibrations[1:]

    return DataAndMetadata.DataAndMetadata(calculate_data, data_shape_and_dtype,
                                           data_and_metadata.intensity_calibration, dimensional_calibrations,
                                           data_and_metadata.metadata, datetime.datetime.utcnow())


def function_pick(data_and_metadata, position):
    data_shape = data_and_metadata.data_shape
    data_dtype = data_and_metadata.data_dtype

    def calculate_data():
        data = data_and_metadata.data
        if not Image.is_data_valid(data):
            return None
        data_shape = data_and_metadata.data_shape
        position_f = Geometry.FloatPoint.make(position)
        position_i = Geometry.IntPoint(y=position_f.y * data_shape[1], x=position_f.x * data_shape[2])
        if position_i.y >= 0 and position_i.y < data_shape[1] and position_i.x >= 0 and position_i.x < data_shape[2]:
            return data[:, position_i[0], position_i[1]].copy()
        else:
            return numpy.zeros((data_shape[:-2], ), dtype=data.dtype)

    dimensional_calibrations = data_and_metadata.dimensional_calibrations

    if not Image.is_shape_and_dtype_valid(data_shape, data_dtype) or dimensional_calibrations is None:
        return None

    data_shape_and_dtype = data_shape[:-2], data_dtype

    dimensional_calibrations = dimensional_calibrations[0:-2]

    return DataAndMetadata.DataAndMetadata(calculate_data, data_shape_and_dtype,
                                           data_and_metadata.intensity_calibration, dimensional_calibrations,
                                           data_and_metadata.metadata, datetime.datetime.utcnow())


def function_project(data_and_metadata):
    data_shape = data_and_metadata.data_shape
    data_dtype = data_and_metadata.data_dtype

    def calculate_data():
        data = data_and_metadata.data
        if not Image.is_data_valid(data):
            return None
        if Image.is_shape_and_dtype_rgb_type(data.shape, data.dtype):
            if Image.is_shape_and_dtype_rgb(data.shape, data.dtype):
                rgb_image = numpy.empty(data.shape[1:], numpy.uint8)
                rgb_image[:,0] = numpy.average(data[...,0], 0)
                rgb_image[:,1] = numpy.average(data[...,1], 0)
                rgb_image[:,2] = numpy.average(data[...,2], 0)
                return rgb_image
            else:
                rgba_image = numpy.empty(data.shape[1:], numpy.uint8)
                rgba_image[:,0] = numpy.average(data[...,0], 0)
                rgba_image[:,1] = numpy.average(data[...,1], 0)
                rgba_image[:,2] = numpy.average(data[...,2], 0)
                rgba_image[:,3] = numpy.average(data[...,3], 0)
                return rgba_image
        else:
            return numpy.sum(data, 0)

    dimensional_calibrations = data_and_metadata.dimensional_calibrations

    if not Image.is_shape_and_dtype_valid(data_shape, data_dtype) or dimensional_calibrations is None:
        return None

    data_shape_and_dtype = data_shape[1:], data_dtype

    dimensional_calibrations = dimensional_calibrations[1:]

    return DataAndMetadata.DataAndMetadata(calculate_data, data_shape_and_dtype,
                                           data_and_metadata.intensity_calibration, dimensional_calibrations,
                                           data_and_metadata.metadata, datetime.datetime.utcnow())


def function_resample_2d(data_and_metadata, height, width):
    height = int(height)
    width = int(width)

    data_shape = data_and_metadata.data_shape
    data_dtype = data_and_metadata.data_dtype

    def calculate_data():
        data = data_and_metadata.data
        if not Image.is_data_valid(data):
            return None
        if not Image.is_data_2d(data):
            return None
        if data.shape[0] == height and data.shape[1] == width:
            return data.copy()
        return Image.scaled(data, (height, width))

    dimensional_calibrations = data_and_metadata.dimensional_calibrations

    if not Image.is_shape_and_dtype_valid(data_shape, data_dtype) or dimensional_calibrations is None:
        return None

    if not Image.is_shape_and_dtype_2d(data_shape, data_dtype):
        return None

    if Image.is_shape_and_dtype_rgb_type(data_shape, data_dtype):
        data_shape_and_dtype = (height, width, data_shape[-1]), data_dtype
    else:
        data_shape_and_dtype = (height, width), data_dtype

    dimensions = height, width
    resampled_dimensional_calibrations = [Calibration.Calibration(dimensional_calibrations[i].offset, dimensional_calibrations[i].scale * data_shape[i] / dimensions[i], dimensional_calibrations[i].units) for i in range(len(dimensional_calibrations))]

    return DataAndMetadata.DataAndMetadata(calculate_data, data_shape_and_dtype,
                                           data_and_metadata.intensity_calibration, resampled_dimensional_calibrations,
                                           data_and_metadata.metadata, datetime.datetime.utcnow())


def function_histogram(data_and_metadata, bins):
    bins = int(bins)

    data_shape = data_and_metadata.data_shape
    data_dtype = data_and_metadata.data_dtype

    def calculate_data():
        data = data_and_metadata.data
        if not Image.is_data_valid(data):
            return None
        histogram_data = numpy.histogram(data, bins=bins)
        return histogram_data[0].astype(numpy.int)

    dimensional_calibrations = data_and_metadata.dimensional_calibrations

    if not Image.is_shape_and_dtype_valid(data_shape, data_dtype) or dimensional_calibrations is None:
        return None

    data_shape_and_dtype = (bins, ), numpy.dtype(numpy.int)

    dimensional_calibrations = [Calibration.Calibration()]

    return DataAndMetadata.DataAndMetadata(calculate_data, data_shape_and_dtype,
                                           data_and_metadata.intensity_calibration, dimensional_calibrations,
                                           data_and_metadata.metadata, datetime.datetime.utcnow())


def function_line_profile(data_and_metadata, vector, integration_width):
    integration_width = int(integration_width)

    data_shape = data_and_metadata.data_shape
    data_dtype = data_and_metadata.data_dtype

    # calculate grid of coordinates. returns n coordinate arrays for each row.
    # start and end are in data coordinates.
    # n is a positive integer, not zero
    def get_coordinates(start, end, n):
        assert n > 0 and int(n) == n
        # n=1 => 0
        # n=2 => -0.5, 0.5
        # n=3 => -1, 0, 1
        # n=4 => -1.5, -0.5, 0.5, 1.5
        length = math.sqrt(math.pow(end[0] - start[0], 2) + math.pow(end[1] - start[1], 2))
        l = math.floor(length)
        a = numpy.linspace(0, length, l)  # along
        t = numpy.linspace(-(n-1)*0.5, (n-1)*0.5, n)  # transverse
        dy = (end[0] - start[0]) / length
        dx = (end[1] - start[1]) / length
        ix, iy = numpy.meshgrid(a, t)
        yy = start[0] + dy * ix + dx * iy
        xx = start[1] + dx * ix - dy * iy
        return xx, yy

    # xx, yy = __coordinates(None, (4,4), (8,4), 3)

    def calculate_data():
        data = data_and_metadata.data
        if not Image.is_data_valid(data):
            return None
        if Image.is_data_rgb_type(data):
            data = Image.convert_to_grayscale(data, numpy.double)
        start, end = vector
        shape = data.shape
        actual_integration_width = min(max(shape[0], shape[1]), integration_width)  # limit integration width to sensible value
        start_data = (int(shape[0]*start[0]), int(shape[1]*start[1]))
        end_data = (int(shape[0]*end[0]), int(shape[1]*end[1]))
        length = math.sqrt(math.pow(end_data[1] - start_data[1], 2) + math.pow(end_data[0] - start_data[0], 2))
        if length > 1.0:
            spline_order_lookup = { "nearest": 0, "linear": 1, "quadratic": 2, "cubic": 3 }
            method = "nearest"
            spline_order = spline_order_lookup[method]
            xx, yy = get_coordinates(start_data, end_data, actual_integration_width)
            samples = scipy.ndimage.map_coordinates(data, (yy, xx), order=spline_order)
            if len(samples.shape) > 1:
                return numpy.sum(samples, 0) / actual_integration_width
            else:
                return samples
        return numpy.zeros((1))

    dimensional_calibrations = data_and_metadata.dimensional_calibrations

    if not Image.is_shape_and_dtype_valid(data_shape, data_dtype) or dimensional_calibrations is None:
        return None

    if dimensional_calibrations is None or len(dimensional_calibrations) != 2:
        return None

    import math

    start, end = vector
    shape = data_shape
    start_int = (int(shape[0]*start[0]), int(shape[1]*start[1]))
    end_int = (int(shape[0]*end[0]), int(shape[1]*end[1]))
    length = int(math.sqrt((end_int[1] - start_int[1])**2 + (end_int[0] - start_int[0])**2))
    length = max(length, 1)
    data_shape_and_dtype = (length, ), numpy.dtype(numpy.double)

    dimensional_calibrations = [Calibration.Calibration(0.0, dimensional_calibrations[1].scale, dimensional_calibrations[1].units)]

    return DataAndMetadata.DataAndMetadata(calculate_data, data_shape_and_dtype,
                                           data_and_metadata.intensity_calibration, dimensional_calibrations,
                                           data_and_metadata.metadata, datetime.datetime.utcnow())


_function2_map = {
    "fft": function_fft,
    "ifft": function_ifft,
    "autocorrelate": function_autocorrelate,
    "crosscorrelate": function_crosscorrelate,
    "sobel": function_sobel,
    "laplace": function_laplace,
    "gaussian_blur": function_gaussian_blur,
    "median_filter": function_median_filter,
    "uniform_filter": function_uniform_filter,
    "transpose_flip": function_transpose_flip,
    "crop": function_crop,
    "slice_sum": function_slice_sum,
    "pick": function_pick,
    "project": function_project,
    "resample_image": function_resample_2d,
    "histogram": function_histogram,
    "line_profile": function_line_profile,
}

_function_map = {
    "abs": operator.abs,
    "neg": operator.neg,
    "pos": operator.pos,
    "add": operator.add,
    "sub": operator.sub,
    "mul": operator.mul,
    "div": operator.truediv,
    "truediv": operator.truediv,
    "floordiv": operator.floordiv,
    "mod": operator.mod,
    "pow": operator.pow,
    "slice": take_slice,
    "amin": numpy.amin,
    "amax": numpy.amax,
    "arange": arange,
    "median": numpy.median,
    "average": numpy.average,
    "mean": numpy.mean,
    "std": numpy.std,
    "var": numpy.var,
    "log": numpy.log,
    "log10": numpy.log10,
    "log2": numpy.log2,
}


def extract_data(evaluated_input):
    if isinstance(evaluated_input, DataAndMetadata.DataAndMetadata):
        return evaluated_input.data
    return evaluated_input


class DataNode(object):

    def __init__(self, inputs=None):
        self.uuid = uuid.uuid4()
        self.inputs = inputs if inputs is not None else list()

    @classmethod
    def factory(cls, d):
        data_node_type = d["data_node_type"]
        assert data_node_type in _node_map
        node = _node_map[data_node_type]()
        node.read(d)
        return node

    def read(self, d):
        self.uuid = uuid.UUID(d["uuid"])
        inputs = list()
        input_dicts = d.get("inputs", list())
        for input_dict in input_dicts:
            node = DataNode.factory(input_dict)
            node.read(input_dict)
            inputs.append(node)
        self.inputs = inputs
        return d

    def write(self):
        d = dict()
        d["uuid"] = str(self.uuid)
        input_dicts = list()
        for input in self.inputs:
            input_dicts.append(input.write())
        if len(input_dicts) > 0:
            d["inputs"] = input_dicts
        return d

    @classmethod
    def make(cls, value):
        if isinstance(value, DataNode):
            return value
        elif isinstance(value, numbers.Integral):
            return ConstantDataNode(value)
        elif isinstance(value, numbers.Rational):
            return ConstantDataNode(value)
        elif isinstance(value, numbers.Real):
            return ConstantDataNode(value)
        elif isinstance(value, numbers.Complex):
            return ConstantDataNode(value)
        elif isinstance(value, DataItemDataNode):
            return value
        assert False
        return None

    def evaluate(self, context):
        evaluated_inputs = list()
        for input in self.inputs:
            evaluated_input = input.evaluate(context)
            evaluated_inputs.append(evaluated_input)
        return self._evaluate_inputs(evaluated_inputs, context)

    def _evaluate_inputs(self, evaluated_inputs, context):
        raise NotImplementedError()

    def bind(self, context, bound_items):
        for input in self.inputs:
            input.bind(context, bound_items)

    def unbind(self):
        for input in self.inputs:
            input.unbind()

    def print_mapping(self, context):
        for input in self.inputs:
            input.print_mapping(context)

    def __abs__(self):
        return UnaryOperationDataNode([self], "abs")

    def __neg__(self):
        return UnaryOperationDataNode([self], "neg")

    def __pos__(self):
        return UnaryOperationDataNode([self], "pos")

    def __add__(self, other):
        return BinaryOperationDataNode([self, DataNode.make(other)], "add")

    def __radd__(self, other):
        return BinaryOperationDataNode([DataNode.make(other), self], "add")

    def __sub__(self, other):
        return BinaryOperationDataNode([self, DataNode.make(other)], "sub")

    def __rsub__(self, other):
        return BinaryOperationDataNode([DataNode.make(other), self], "sub")

    def __mul__(self, other):
        return BinaryOperationDataNode([self, DataNode.make(other)], "mul")

    def __rmul__(self, other):
        return BinaryOperationDataNode([DataNode.make(other), self], "mul")

    def __div__(self, other):
        return BinaryOperationDataNode([self, DataNode.make(other)], "div")

    def __rdiv__(self, other):
        return BinaryOperationDataNode([DataNode.make(other), self], "div")

    def __truediv__(self, other):
        return BinaryOperationDataNode([self, DataNode.make(other)], "truediv")

    def __rtruediv__(self, other):
        return BinaryOperationDataNode([DataNode.make(other), self], "truediv")

    def __floordiv__(self, other):
        return BinaryOperationDataNode([self, DataNode.make(other)], "floordiv")

    def __rfloordiv__(self, other):
        return BinaryOperationDataNode([DataNode.make(other), self], "floordiv")

    def __mod__(self, other):
        return BinaryOperationDataNode([self, DataNode.make(other)], "mod")

    def __rmod__(self, other):
        return BinaryOperationDataNode([DataNode.make(other), self], "mod")

    def __pow__(self, other):
        return BinaryOperationDataNode([self, DataNode.make(other)], "pow")

    def __rpow__(self, other):
        return BinaryOperationDataNode([DataNode.make(other), self], "pow")

    def __complex__(self):
        return ConstantDataNode(numpy.astype(numpy.complex128))

    def __int__(self):
        return ConstantDataNode(numpy.astype(numpy.uint32))

    def __long__(self):
        return ConstantDataNode(numpy.astype(numpy.int64))

    def __float__(self):
        return ConstantDataNode(numpy.astype(numpy.float64))

    def __getitem__(self, key):
        return UnaryOperationDataNode([self], "slice", {"key": key})


class ConstantDataNode(DataNode):

    def __init__(self, value=None):
        super(ConstantDataNode, self).__init__()
        self.__scalar = numpy.array(value)
        if isinstance(value, numbers.Integral):
            self.__scalar_type = "integral"
        elif isinstance(value, numbers.Rational):
            self.__scalar_type = "rational"
        elif isinstance(value, numbers.Real):
            self.__scalar_type = "real"
        elif isinstance(value, numbers.Complex):
            self.__scalar_type = "complex"
        # else:
        #     raise Exception("Invalid constant type [{}].".format(type(value)))

    def read(self, d):
        super(ConstantDataNode, self).read(d)
        scalar_type = d.get("scalar_type")
        if scalar_type == "integral":
            self.__scalar = numpy.array(int(d["value"]))
        elif scalar_type == "real":
            self.__scalar = numpy.array(float(d["value"]))
        elif scalar_type == "complex":
            self.__scalar = numpy.array(complex(*d["value"]))

    def write(self):
        d = super(ConstantDataNode, self).write()
        d["data_node_type"] = "constant"
        d["scalar_type"] = self.__scalar_type
        value = self.__scalar
        if self.__scalar_type == "integral":
            d["value"] = int(value)
        elif isinstance(value, numbers.Rational):
            pass
        elif self.__scalar_type == "real":
            d["value"] = float(value)
        elif self.__scalar_type == "complex":
            d["value"] = complex(float(value.real), float(value.imag))
        return d

    def _evaluate_inputs(self, evaluated_inputs, context):
        return self.__scalar

    def __str__(self):
        return "{0} ({1})".format(self.__repr__(), self.__scalar)


class ScalarOperationDataNode(DataNode):

    def __init__(self, inputs=None, function_id=None, args=None):
        super(ScalarOperationDataNode, self).__init__(inputs=inputs)
        self.__function_id = function_id
        self.__args = copy.copy(args if args is not None else dict())

    def read(self, d):
        super(ScalarOperationDataNode, self).read(d)
        function_id = d.get("function_id")
        assert function_id in _function_map
        self.__function_id = function_id
        args = d.get("args")
        self.__args = copy.copy(args if args is not None else dict())

    def write(self):
        d = super(ScalarOperationDataNode, self).write()
        d["data_node_type"] = "scalar"
        d["function_id"] = self.__function_id
        if self.__args:
            d["args"] = self.__args
        return d

    def _evaluate_inputs(self, evaluated_inputs, context):
        def calculate_data():
            return _function_map[self.__function_id](extract_data(evaluated_inputs[0]), **self.__args)

        return DataAndMetadata.DataAndMetadata(calculate_data, evaluated_inputs[0].data_shape_and_dtype,
                                               Calibration.Calibration(), list(), dict(), datetime.datetime.utcnow())

    def __str__(self):
        return "{0} {1}({2})".format(self.__repr__(), self.__function_id, self.inputs[0])


class UnaryOperationDataNode(DataNode):

    def __init__(self, inputs=None, function_id=None, args=None):
        super(UnaryOperationDataNode, self).__init__(inputs=inputs)
        self.__function_id = function_id
        self.__args = copy.copy(args if args is not None else dict())

    def read(self, d):
        super(UnaryOperationDataNode, self).read(d)
        function_id = d.get("function_id")
        assert function_id in _function_map
        self.__function_id = function_id
        args = d.get("args")
        self.__args = copy.copy(args if args is not None else dict())

    def write(self):
        d = super(UnaryOperationDataNode, self).write()
        d["data_node_type"] = "unary"
        d["function_id"] = self.__function_id
        if self.__args:
            d["args"] = self.__args
        return d

    def _evaluate_inputs(self, evaluated_inputs, context):
        def calculate_data():
            return _function_map[self.__function_id](extract_data(evaluated_inputs[0]), **self.__args)

        return DataAndMetadata.DataAndMetadata(calculate_data, evaluated_inputs[0].data_shape_and_dtype,
                                               evaluated_inputs[0].intensity_calibration,
                                               evaluated_inputs[0].dimensional_calibrations,
                                               evaluated_inputs[0].metadata, datetime.datetime.utcnow())

    def __str__(self):
        return "{0} {1}({2})".format(self.__repr__(), self.__function_id, self.inputs[0])


class BinaryOperationDataNode(DataNode):

    def __init__(self, inputs=None, function_id=None, args=None):
        super(BinaryOperationDataNode, self).__init__(inputs=inputs)
        self.__function_id = function_id
        self.__args = copy.copy(args if args is not None else dict())

    def read(self, d):
        super(BinaryOperationDataNode, self).read(d)
        function_id = d.get("function_id")
        assert function_id in _function_map
        self.__function_id = function_id
        args = d.get("args")
        self.__args = copy.copy(args if args is not None else dict())

    def write(self):
        d = super(BinaryOperationDataNode, self).write()
        d["data_node_type"] = "binary"
        d["function_id"] = self.__function_id
        if self.__args:
            d["args"] = self.__args
        return d

    def _evaluate_inputs(self, evaluated_inputs, context):
        def calculate_data():
            return _function_map[self.__function_id](extract_data(evaluated_inputs[0]), extract_data(evaluated_inputs[1]), **self.__args)

        # if the first input is not a data_and_metadata, use the second input
        src_evaluated_input = evaluated_inputs[0] if isinstance(evaluated_inputs[0], DataAndMetadata.DataAndMetadata) else evaluated_inputs[1]

        return DataAndMetadata.DataAndMetadata(calculate_data, src_evaluated_input.data_shape_and_dtype,
                                               src_evaluated_input.intensity_calibration,
                                               src_evaluated_input.dimensional_calibrations,
                                               src_evaluated_input.metadata, datetime.datetime.utcnow())

    def __str__(self):
        return "{0} {1}({2}, {3})".format(self.__repr__(), self.__function_id, self.inputs[0], self.inputs[1])


class FunctionOperationDataNode(DataNode):

    def __init__(self, inputs=None, function_id=None, args=None):
        super(FunctionOperationDataNode, self).__init__(inputs=inputs)
        self.__function_id = function_id
        self.__args = copy.copy(args if args is not None else dict())

    def read(self, d):
        super(FunctionOperationDataNode, self).read(d)
        function_id = d.get("function_id")
        assert function_id in _function2_map
        self.__function_id = function_id
        args = d.get("args")
        self.__args = copy.copy(args if args is not None else dict())

    def write(self):
        d = super(FunctionOperationDataNode, self).write()
        d["data_node_type"] = "function"
        d["function_id"] = self.__function_id
        if self.__args:
            d["args"] = self.__args
        return d

    def _evaluate_inputs(self, evaluated_inputs, context):
        # don't pass the data; the functions are responsible for extracting the data correctly
        return _function2_map[self.__function_id](*evaluated_inputs, **self.__args)

    def __str__(self):
        return "{0} {1}({2}, {3})".format(self.__repr__(), self.__function_id, [str(input) for input in self.inputs], list(self.__args))


class DataItemDataNode(DataNode):

    def __init__(self, object_specifier=None):
        super(DataItemDataNode, self).__init__()
        self.__object_specifier = object_specifier
        self.__bound_item = None

    def read(self, d):
        super(DataItemDataNode, self).read(d)
        self.__object_specifier = d["object_specifier"]

    def write(self):
        d = super(DataItemDataNode, self).write()
        d["data_node_type"] = "data"
        d["object_specifier"] = copy.deepcopy(self.__object_specifier)
        return d

    def _evaluate_inputs(self, evaluated_inputs, context):
        return self.__bound_item.value

    def print_mapping(self, context):
        logging.debug("%s: %s", self.__data_reference_uuid, self.__object_specifier)

    def bind(self, context, bound_items):
        self.__bound_item = context.resolve_object_specifier(self.__object_specifier)
        assert self.__bound_item is not None
        bound_items[self.uuid] = self.__bound_item

    def unbind(self):
        self.__bound_item = None

    def __str__(self):
        return "{0} ({1})".format(self.__repr__(), self.__object_specifier)


class ReferenceDataNode(DataNode):

    def __init__(self, object_specifier=None):
        super(ReferenceDataNode, self).__init__()
        self.__object_specifier = object_specifier

    def read(self, d):
        raise NotImplemented()  # should only be used as intermediate node

    def write(self):
        raise NotImplemented()  # should only be used as intermediate node

    def print_mapping(self):
        raise NotImplemented()  # should only be used as intermediate node

    def bind(self, context, bound_items):
        raise NotImplemented()  # should only be used as intermediate node

    def unbind(self):
        raise NotImplemented()  # should only be used as intermediate node

    def __getattr__(self, name):
        return PropertyDataNode(self.__object_specifier, name)

    def __str__(self):
        return "{0} ({1})".format(self.__repr__(), self.__reference_uuid)


class PropertyDataNode(DataNode):

    def __init__(self, object_specifier=None, property=None):
        super(PropertyDataNode, self).__init__()
        self.__object_specifier = object_specifier
        self.__property = str(property)
        self.__bound_item = None

    def read(self, d):
        super(PropertyDataNode, self).read(d)
        self.__object_specifier = d["object_specifier"]
        self.__property = d["property"]

    def write(self):
        d = super(PropertyDataNode, self).write()
        d["data_node_type"] = "property"
        d["object_specifier"] = copy.deepcopy(self.__object_specifier)
        d["property"] = self.__property
        return d

    def _evaluate_inputs(self, evaluated_inputs, resolve):
        return self.__bound_item.value

    def print_mapping(self, context):
        logging.debug("%s.%s: %s", self.__reference_uuid, self.__property, self.__object_specifier)

    def bind(self, context, bound_items):
        self.__bound_item = context.resolve_object_specifier(self.__object_specifier, self.__property)
        assert self.__bound_item is not None
        bound_items[self.uuid] = self.__bound_item

    def unbind(self):
        self.__bound_item = None

    def __str__(self):
        return "{0} ({1}.{2})".format(self.__repr__(), self.__object_specifier, self.__property)


_node_map = {
    "constant": ConstantDataNode,
    "scalar": ScalarOperationDataNode,
    "unary": UnaryOperationDataNode,
    "binary": BinaryOperationDataNode,
    "function": FunctionOperationDataNode,
    "property": PropertyDataNode,
    "reference": ReferenceDataNode,
    "data": DataItemDataNode,
}


def parse_expression(calculation_script, variable_map):
    code_lines = []
    g = dict()
    g["amin"] = lambda data_node: ScalarOperationDataNode([data_node], "amin")
    g["amax"] = lambda data_node: ScalarOperationDataNode([data_node], "amax")
    g["arange"] = lambda data_node: ScalarOperationDataNode([data_node], "arange")
    g["median"] = lambda data_node: ScalarOperationDataNode([data_node], "median")
    g["average"] = lambda data_node: ScalarOperationDataNode([data_node], "average")
    g["mean"] = lambda data_node: ScalarOperationDataNode([data_node], "mean")
    g["std"] = lambda data_node: ScalarOperationDataNode([data_node], "std")
    g["var"] = lambda data_node: ScalarOperationDataNode([data_node], "var")
    g["log"] = lambda data_node: UnaryOperationDataNode([data_node], "log")
    g["log10"] = lambda data_node: UnaryOperationDataNode([data_node], "log10")
    g["log2"] = lambda data_node: UnaryOperationDataNode([data_node], "log2")
    g["fft"] = lambda data_node: FunctionOperationDataNode([data_node], "fft")
    g["ifft"] = lambda data_node: FunctionOperationDataNode([data_node], "ifft")
    g["autocorrelate"] = lambda data_node: FunctionOperationDataNode([data_node], "autocorrelate")
    g["crosscorrelate"] = lambda data_node1, data_node2: FunctionOperationDataNode([data_node1, data_node2], "crosscorrelate")
    g["sobel"] = lambda data_node: FunctionOperationDataNode([data_node], "sobel")
    g["laplace"] = lambda data_node: FunctionOperationDataNode([data_node], "laplace")
    g["gaussian_blur"] = lambda data_node, scalar_node: FunctionOperationDataNode([data_node, DataNode.make(scalar_node)], "gaussian_blur")
    g["median_filter"] = lambda data_node, scalar_node: FunctionOperationDataNode([data_node, DataNode.make(scalar_node)], "median_filter")
    g["uniform_filter"] = lambda data_node, scalar_node: FunctionOperationDataNode([data_node, DataNode.make(scalar_node)], "uniform_filter")
    def transpose_flip(data_node, transpose=False, flip_v=False, flip_h=False):
        return FunctionOperationDataNode([data_node], "transpose_flip", args={"transpose": transpose, "flip_v": flip_v, "flip_h": flip_h})
    g["transpose_flip"] = transpose_flip
    g["crop"] = lambda data_node, bounds_node: FunctionOperationDataNode([data_node, bounds_node], "crop")
    g["slice_sum"] = lambda data_node, scalar_node1, scalar_node2: FunctionOperationDataNode([data_node, DataNode.make(scalar_node1), DataNode.make(scalar_node2)], "slice_sum")
    g["pick"] = lambda data_node, position_node: FunctionOperationDataNode([data_node, position_node], "pick")
    g["project"] = lambda data_node, position_node: FunctionOperationDataNode([data_node, position_node], "project")
    g["resample_image"] = lambda data_node, position_node: FunctionOperationDataNode([data_node, position_node], "resample_image")
    g["histogram"] = lambda data_node, position_node: FunctionOperationDataNode([data_node, position_node], "histogram")
    g["line_profile"] = lambda data_node, position_node: FunctionOperationDataNode([data_node, position_node], "line_profile")
    l = dict()
    for variable_name, object_specifier in variable_map.items():
        if object_specifier["type"] == "data_item":  # avoid importing class
            reference_node = DataItemDataNode(object_specifier=object_specifier)
        else:
            reference_node = ReferenceDataNode(object_specifier=object_specifier)
        g[variable_name] = reference_node
    code_lines.append("result = {0}".format(calculation_script))
    code = "\n".join(code_lines)
    try:
        exec(code, g, l)
    except Exception as e:
        return None
    return l["result"]


class Computation(Observable.Observable, Observable.ManagedObject):
    """A computation on data and other inputs using symbolic nodes.

    Watches for changes to the sources and fires a needs_update_event
    when a new computation needs to occur.

    Call parse_expression first to establish the computation. Bind will be automatically called.

    Call bind to establish connections after reloading. Call unbind to release connections.

    Listen to needs_update_event and call evaluate in response to perform
    computation (on thread).

    The computation will listen to any bound items established in the bind method. When those
    items signal a change, the needs_update_event will be fired.
    """

    def __init__(self):
        super(Computation, self).__init__()
        self.define_type("computation")
        self.define_property("node")
        self.__bound_items = dict()
        self.__bound_item_listeners = dict()
        self.__data_node = None
        self.needs_update_event = Observable.Event()

    def read_from_dict(self, properties):
        super(Computation, self).read_from_dict(properties)
        self.__data_node = DataNode.factory(self.node)

    def parse_expression(self, context, expression, variable_map):
        self.__data_node = parse_expression(expression, variable_map)
        if self.__data_node:
            self.node = self.__data_node.write()
            self.bind(context)

    def evaluate(self):
        def resolve(uuid):
            bound_item = self.__bound_items[uuid]
            return bound_item.value
        if self.__data_node:
            return self.__data_node.evaluate(resolve)
        return None

    def bind(self, context):
        assert len(self.__bound_items) == 0
        self.__data_node.bind(context, self.__bound_items)
        def needs_update():
            self.needs_update_event.fire()
        for bound_item_uuid, bound_item in self.__bound_items.items():
            self.__bound_item_listeners[bound_item_uuid] = bound_item.changed_event.listen(needs_update)

    def unbind(self):
        for bound_item, bound_item_listener in zip(self.__bound_items.values(), self.__bound_item_listeners.values()):
            bound_item.close()
            bound_item_listener.close()
        self.__bound_items = dict()
        self.__bound_item_listeners = dict()
