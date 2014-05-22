# standard libraries
import collections
import copy
import datetime
import gettext
import logging
import os
import threading
import uuid
import weakref

# third party libraries
import numpy

# local libraries
from nion.swift.model import Calibration
from nion.swift.model import DataItemProcessor
from nion.swift.model import Display
from nion.swift.model import Image
from nion.swift.model import Operation
from nion.swift.model import Storage
from nion.swift.model import Utility
from nion.ui import Observable
from nion.ui import ThreadPool

_ = gettext.gettext


class StatisticsDataItemProcessor(DataItemProcessor.DataItemProcessor):

    def __init__(self, data_item):
        super(StatisticsDataItemProcessor, self).__init__(data_item, "statistics_data")

    def get_calculated_data(self, ui, data):
        #logging.debug("Calculating statistics %s", self)
        mean = numpy.mean(data)
        std = numpy.std(data)
        data_min, data_max = self.item.data_range
        all_computations = { "mean": mean, "std": std, "min": data_min, "max": data_max }
        global _computation_fns
        for computation_fn in _computation_fns:
            computations = computation_fn(self.item)
            if computations is not None:
                all_computations.update(computations)
        return all_computations

    def get_default_data(self):
        return { }

    def get_data_item(self):
        return self.item


class CalibrationList(object):

    def __init__(self):
        self.list = list()

    def read_dict(self, storage_list):
        # storage_list will be whatever is returned by write_dict.
        new_list = list()
        for calibration_dict in storage_list:
            new_list.append(Calibration.Calibration().read_dict(calibration_dict))
        self.list = new_list
        return self  # for convenience

    def write_dict(self):
        list = []
        for calibration in self.list:
            list.append(calibration.write_dict())
        return list


class DataSourceList(object):

    def __init__(self):
        self.list = list()

    def read_dict(self, storage_list):
        # storage_list will be whatever is returned by write_dict.
        self.list = copy.copy(storage_list)
        return self  # for convenience

    def write_dict(self):
        return copy.copy(self.list)


class DataItemVault(object):

    def __init__(self, data_item, storage_dict):
        self.__weak_data_item = weakref.ref(data_item)
        self.storage_dict = storage_dict

    def close(self):
        self.__weak_data_item = None

    def __get_data_item(self):
        return self.__weak_data_item() if self.__weak_data_item else None
    data_item = property(__get_data_item)

    def insert_item(self, name, before_index, item):
        with self.data_item.property_changes() as pc:
            item_list = self.storage_dict.setdefault(name, list())
            item_dict = dict()
            item_list.insert(before_index, item_dict)
            item.vault = DataItemVault(self.data_item, item_dict)
            item.write_storage(DataItemVault(self.data_item, item_dict))

    def remove_item(self, name, index, item):
        with self.data_item.property_changes() as pc:
            item_list = self.storage_dict[name]
            del item_list[index]

    def set_value(self, name, value):
        with self.data_item.property_changes() as pc:
            self.storage_dict[name] = value

    def get_vault_for_item(self, name, index):
        storage_dict = self.storage_dict[name][index]
        return DataItemVault(self.data_item, storage_dict)

    def has_value(self, name):
        return name in self.storage_dict

    def get_value(self, name):
        return self.storage_dict[name]

    def get_item_vaults(self, name):
        if name in self.storage_dict:
            return [DataItemVault(self.data_item, storage_dict) for storage_dict in self.storage_dict[name]]
        return list()


# data items will represents a numpy array. the numpy array
# may be stored directly in this item (master data), or come
# from another data item (data source).

# thumbnail: a small representation of this data item

# displays: list of displays for this data item

# intrinsic_calibrations: calibration for each dimension

# data: data with all operations applied

# master data: a numpy array associated with this data item
# data source: another data item from which data is taken

# data range: cached value for data min/max. calculated when data is requested, or on demand.

# operations: a list of operations applied to make data

# data items: child data items (aka derived data)

# cached data: holds last result of data calculation

# last cached data: holds last valid cached data

# best data: returns the best data available without doing a calculation

# live data: a bool indicating whether the data is live

# data is calculated when requested. this makes it imperative that callers
# do not ask for data to be calculated on the main thread.

# values that are cached will be marked as dirty when they don't match
# the underlying data. however, the values will still return values for
# the out of date data.


# enumerations for types of changes
DATA = 1
METADATA = 2
DISPLAYS = 3
SOURCE = 4


# dates are _local_ time and must use this specific ISO 8601 format. 2013-11-17T08:43:21.389391
# time zones are offsets (east of UTC) in the following format "+HHMM" or "-HHMM"
# daylight savings times are time offset (east of UTC) in format "+MM" or "-MM"
# time zone name is for display only and has no specified format

class DataItem(Storage.StorageBase, Observable.ActiveSerializable):

    def __init__(self, data=None, properties=None, create_display=True):
        super(DataItem, self).__init__()
        self.storage_properties += ["properties"]
        self.storage_data_keys += ["master_data"]
        self.storage_type = "data-item"
        current_datetime_item = Utility.get_current_datetime_item()
        spatial_calibrations = CalibrationList()
        if data is not None:
            spatial_calibrations.list.extend([Calibration.Calibration() for i in xrange(len(Image.spatial_shape_from_shape_and_dtype(data.shape, data.dtype)))])
        self.define_property(Observable.Property("intrinsic_intensity_calibration", Calibration.Calibration(), make=Calibration.Calibration, changed=self.__intrinsic_intensity_calibration_changed))
        self.define_property(Observable.Property("intrinsic_spatial_calibrations", spatial_calibrations, make=CalibrationList, changed=self.__intrinsic_spatial_calibrations_changed))
        self.define_property(Observable.Property("datetime_original", current_datetime_item, validate=self.__validate_datetime, changed=self.__metadata_property_changed))
        self.define_property(Observable.Property("datetime_modified", current_datetime_item, validate=self.__validate_datetime, changed=self.__metadata_property_changed))
        self.define_property(Observable.Property("title", _("Untitled"), validate=self.__validate_title, changed=self.__metadata_property_changed))
        self.define_property(Observable.Property("caption", unicode(), validate=self.__validate_caption, changed=self.__metadata_property_changed))
        self.define_property(Observable.Property("rating", 0, validate=self.__validate_rating, changed=self.__metadata_property_changed))
        self.define_property(Observable.Property("flag", 0, validate=self.__validate_flag, changed=self.__metadata_property_changed))
        self.define_property(Observable.Property("source_file_path", validate=self.__validate_source_file_path, changed=self.__property_changed))
        self.define_property(Observable.Property("session_id", validate=self.__validate_session_id, changed=self.__session_id_changed))
        self.define_property(Observable.Property("data_sources", DataSourceList(), make=DataSourceList))
        self.define_relationship(Observable.Relationship("operations", Operation.operation_item_factory, insert=self.__insert_operation, remove=self.__remove_operation))
        self.define_relationship(Observable.Relationship("displays", Display.display_factory, insert=self.__insert_display, remove=self.__remove_display))
        self.__metadata = dict()
        self.closed = False
        # data is immutable but metadata isn't, keep track of original and modified dates
        self.__properties = properties if properties is not None else dict()
        self.__data_mutex = threading.RLock()
        self.__get_data_mutex = threading.RLock()
        self.__cached_data = None
        self.__cached_data_dirty = True
        # master data shape and dtype are always valid if there is no data source.
        self.__master_data = None
        self.__master_data_shape = None
        self.__master_data_dtype = None
        self.__master_data_reference_type = None  # used for temporary storage
        self.__master_data_reference = None  # used for temporary storage
        self.__master_data_file_datetime = None  # used for temporary storage
        self.master_data_save_event = threading.Event()
        self.__has_master_data = False
        self.__data_source = None
        self.__data_ref_count = 0
        self.__data_ref_count_mutex = threading.RLock()
        self.__data_item_change_mutex = threading.RLock()
        self.__data_item_change_count = 0
        self.__data_item_changes = set()
        self.__shared_thread_pool = ThreadPool.create_thread_queue()
        self.__processors = dict()
        self.__processors["statistics"] = StatisticsDataItemProcessor(self)
        self.__set_master_data(data)
        self.vault = DataItemVault(self, self.__properties)
        if create_display:
            self.add_display(Display.Display())  # always have one display, for now

    def __str__(self):
        return "{0} {1} ({2}, {3})".format(self.__repr__(), (self.title if self.title else _("Untitled")), str(self.uuid), self.datetime_original_as_string)

    @classmethod
    def _get_data_file_path(cls, uuid_, datetime_item, session_id=None):
        # uuid_.bytes.encode('base64').rstrip('=\n').replace('/', '_')
        # and back: uuid_ = uuid.UUID(bytes=(slug + '==').replace('_', '/').decode('base64'))
        # also:
        def encode(uuid_, alphabet):
            result = str()
            uuid_int = uuid_.int
            while uuid_int:
                uuid_int, digit = divmod(uuid_int, len(alphabet))
                result += alphabet[digit]
            return result
        encoded_uuid_str = encode(uuid_, "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890")  # 25 character results
        datetime_item = datetime_item if datetime_item else Utility.get_current_datetime_item()
        datetime_ = Utility.get_datetime_from_datetime_item(datetime_item)
        datetime_ = datetime_ if datetime_ else datetime.datetime.now()
        path_components = datetime_.strftime("%Y-%m-%d").split('-')
        session_id = session_id if session_id else datetime_.strftime("%Y%m%d-000000")
        path_components.append(session_id)
        path_components.append("master_data_" + encoded_uuid_str + ".nsdata")
        return os.path.join(*path_components)

    @classmethod
    def build(cls, datastore, item_node, uuid_):
        properties = datastore.get_property(item_node, "properties")
        properties = properties if properties else dict()
        has_master_data = datastore.has_data(item_node, "master_data")
        if has_master_data:
            master_data_shape, master_data_dtype = datastore.get_data_shape_and_dtype(item_node, "master_data")
        else:
            master_data_shape, master_data_dtype = None, None
        data_item = DataItem(properties=properties, create_display=False)
        data_item.__properties = properties
        data_item.__master_data_shape = master_data_shape
        data_item.__master_data_dtype = master_data_dtype
        data_item.__has_master_data = has_master_data
        data_item.read_storage(data_item.vault)
        for key in properties.keys():
            if key not in data_item.property_names and key not in data_item.relationship_names:
                data_item.__metadata.setdefault(key, dict()).update(properties[key])
        data_item.sync_intrinsic_spatial_calibrations()
        assert(len(data_item.displays) > 0)
        return data_item

    # This gets called when reference count goes to 0, but before deletion.
    def about_to_delete(self):
        self.closed = True
        self.__shared_thread_pool.close()
        for operation in self.operations:
            self.remove_operation(operation)
        for display in self.displays:
            self.remove_display(display)
        self.__set_data_source(None)
        self.__set_master_data(None)
        self.undefine_properties()
        self.undefine_relationships()
        self.vault.close()
        super(DataItem, self).about_to_delete()

    def __deepcopy__(self, memo):
        data_item_copy = DataItem(create_display=False)
        # metadata
        data_item_copy.copy_metadata_from(self)
        # calibrations
        data_item_copy.intrinsic_intensity_calibration = self.intrinsic_intensity_calibration
        data_item_copy.intrinsic_spatial_calibrations = self.intrinsic_spatial_calibrations
        # displays
        for display in self.displays:
            data_item_copy.add_display(copy.deepcopy(display))
        # operations
        for operation in self.operations:
            data_item_copy.add_operation(copy.deepcopy(operation))
        # data sources
        data_item_copy.data_sources = self.data_sources
        # data.
        if self.has_master_data:
            with self.data_ref() as data_ref:
                data_item_copy.__set_master_data(numpy.copy(data_ref.master_data))
        else:
            data_item_copy.__set_master_data(None)
        # the data source connection will be established when this copy is inserted.
        memo[id(self)] = data_item_copy
        return data_item_copy

    def copy_metadata_from(self, data_item):
        self.datetime_original = data_item.datetime_original
        self.datetime_modified = data_item.datetime_modified
        self.title = data_item.title
        self.caption = data_item.caption
        self.rating = data_item.rating
        self.flag = data_item.flag
        self.session_id = data_item.session_id
        self.source_file_path = data_item.source_file_path
        for key in data_item.__metadata.keys():
            with self.open_metadata(key) as metadata:
                metadata.clear()
                metadata.update(data_item.get_metadata(key))

    def snapshot(self):
        """
            Take a snapshot and return a new data item. A snapshot is a copy of everything
            except the data and operations which are replaced by new data with the operations
            applied or "burned in".
        """
        data_item_copy = DataItem(create_display=False)
        # metadata
        data_item_copy.copy_metadata_from(self)
        # calibrations
        data_item_copy.set_intensity_calibration(self.calculated_intensity_calibration)
        for index in xrange(len(self.spatial_shape)):
            data_item_copy.set_spatial_calibration(index, self.calculated_calibrations[index])
        # displays
        for display in self.displays:
            data_item_copy.add_display(copy.deepcopy(display))
        # data sources are NOT copied, since this is a snapshot of the data
        data_item_copy.data_sources = DataSourceList()
        # master data. operations are NOT copied, since this is a snapshot of the data
        with self.data_ref() as data_ref:
            data_copy = numpy.copy(data_ref.data)
            data_item_copy.__set_master_data(data_copy)
        return data_item_copy

    def add_shared_task(self, task_id, item, fn):
        self.__shared_thread_pool.add_task(task_id, item, fn)

    def get_processor(self, processor_id):
        return self.__processors[processor_id]

    # cheap, but incorrect, way to tell whether this is live acquisition
    def __get_is_live(self):
        return self.transaction_count > 0
    is_live = property(__get_is_live)

    def __get_live_status_as_string(self):
        if self.is_live:
            live_metadata = self.get_metadata("hardware_source")
            frame_index_str = str(live_metadata.get("frame_index", str()))
            partial_str = "{0:d}/{1:d}".format(live_metadata.get("valid_rows"), self.spatial_shape[-1]) if "valid_rows" in live_metadata else str()
            return "{0:s} {1:s} {2:s}".format(_("Live"), frame_index_str, partial_str)
        return str()
    live_status_as_string = property(__get_live_status_as_string)

    def __validate_session_id(self, value):
        assert value is None or datetime.datetime.strptime(value, "%Y%m%d-%H%M%S")
        return value

    def __session_id_changed(self, name, value):
        self.__property_changed(name, value)

    def data_item_changes(self):
        class DataItemChangeContextManager(object):
            def __init__(self, data_item):
                self.__data_item = data_item
            def __enter__(self):
                self.__data_item.begin_data_item_changes()
                return self
            def __exit__(self, type, value, traceback):
                self.__data_item.end_data_item_changes()
        return DataItemChangeContextManager(self)

    def begin_data_item_changes(self):
        with self.__data_item_change_mutex:
            self.__data_item_change_count += 1

    def end_data_item_changes(self):
        with self.__data_item_change_mutex:
            self.__data_item_change_count -= 1
            data_item_change_count = self.__data_item_change_count
            if data_item_change_count == 0:
                changes = self.__data_item_changes
                self.__data_item_changes = set()
        if data_item_change_count == 0:
            # clear the processor caches
            for processor in self.__processors.values():
                processor.data_item_changed()
            # clear the data cache and preview if the data changed
            if DATA in changes or SOURCE in changes:
                self.__clear_cached_data()
            self.notify_listeners("data_item_content_changed", self, changes)

    def __validate_datetime(self, value):
        return copy.deepcopy(value)

    def __validate_title(self, value):
        return unicode(value)

    def __validate_caption(self, value):
        return unicode(value)

    def __validate_flag(self, value):
        return max(min(int(value), 1), -1)

    def __validate_rating(self, value):
        return min(max(int(value), 0), 5)

    def __validate_source_file_path(self, value):
        return unicode(value)

    def __intrinsic_intensity_calibration_changed(self, name, value):
        self.notify_set_property(name, value)
        self.notify_data_item_content_changed(set([METADATA]))
        self.notify_listeners("data_item_calibration_changed")

    def __metadata_property_changed(self, name, value):
        self.__property_changed(name, value)
        self.__metadata_changed()

    def __metadata_changed(self):
        self.notify_data_item_content_changed(set([METADATA]))

    def __property_changed(self, name, value):
        self.notify_set_property(name, value)

    # call this when the listeners need to be updated (via data_item_content_changed).
    # Calling this method will send the data_item_content_changed method to each listener.
    def notify_data_item_content_changed(self, changes):
        with self.data_item_changes():
            with self.__data_item_change_mutex:
                self.__data_item_changes.update(changes)

    def __get_data_range_for_data(self, data):
        if data is not None and data.size:
            if self.is_data_rgb_type:
                data_range = (0, 255)
            elif Image.is_shape_and_dtype_complex_type(data.shape, data.dtype):
                scalar_data = Image.scalar_from_array(data)
                data_range = (scalar_data.min(), scalar_data.max())
            else:
                data_range = (data.min(), data.max())
        else:
            data_range = None
        if data_range:
            self.set_cached_value("data_range", data_range)
        else:
            self.remove_cached_value("data_range")
        return data_range

    def __get_data_range(self):
        with self.__data_mutex:
            data_range = self.get_cached_value("data_range")
        # this property may be access on the main thread (inspector)
        # so it really needs to return quickly in most cases. don't
        # recalculate in the main thread unless the value doesn't exist
        # at all.
        # TODO: use promises here?
        if self.is_cached_value_dirty("data_range"):
            pass  # TODO: calculate data range in thread
        if not data_range:
            with self.data_ref() as data_ref:
                data = data_ref.data
                data_range = self.__get_data_range_for_data(data)
        return data_range
    data_range = property(__get_data_range)

    # calibration stuff

    def __is_calibrated(self):
        return len(self.intrinsic_calibrations) == len(self.spatial_shape)
    is_calibrated = property(__is_calibrated)

    def set_spatial_calibration(self, dimension, calibration):
        spatial_calibrations = self.intrinsic_spatial_calibrations
        while len(spatial_calibrations.list) <= dimension:
            spatial_calibrations.list.append(Calibration.Calibration())
        spatial_calibrations.list[dimension] = calibration
        self.intrinsic_spatial_calibrations = spatial_calibrations

    def __intrinsic_spatial_calibrations_changed(self, name, value):
        self.notify_data_item_content_changed(set([METADATA]))
        self.notify_listeners("data_item_calibration_changed")


    def set_intensity_calibration(self, calibration):
        self.intrinsic_intensity_calibration = calibration

    def __get_intrinsic_calibrations(self):
        return copy.deepcopy(self.intrinsic_spatial_calibrations.list)
    intrinsic_calibrations = property(__get_intrinsic_calibrations)

    def __get_calculated_intensity_calibration(self):
        # data source calibrations override
        if self.data_source:
            intensity_calibration = self.data_source.calculated_intensity_calibration
        else:
            intensity_calibration = self.intrinsic_intensity_calibration
        data_shape, data_dtype = self.__get_root_data_shape_and_dtype()
        if data_shape is not None and data_dtype is not None:
            for operation in self.operations:
                if operation.enabled:
                    intensity_calibration = operation.get_processed_intensity_calibration(data_shape, data_dtype, intensity_calibration)
                    data_shape, data_dtype = operation.get_processed_data_shape_and_dtype(data_shape, data_dtype)
        return intensity_calibration
    calculated_intensity_calibration = property(__get_calculated_intensity_calibration)

    # call this when data changes. this makes sure that the right number
    # of intrinsic_calibrations exist in this object.
    def sync_intrinsic_spatial_calibrations(self):
        spatial_shape = self.spatial_shape
        ndim = len(spatial_shape) if spatial_shape is not None else 0
        spatial_calibrations = self.intrinsic_spatial_calibrations
        if len(spatial_calibrations.list) != ndim and not self.closed:
            while len(spatial_calibrations.list) < ndim:
                spatial_calibrations.list.append(Calibration.Calibration())
            while len(spatial_calibrations.list) > ndim:
                spatial_calibrations.list.remove(spatial_calibrations.list[-1])
            self.intrinsic_spatial_calibrations = spatial_calibrations

    # calculate the calibrations by starting with the source calibration
    # and then applying calibration transformations for each enabled
    # operation.
    def __get_calculated_calibrations(self):
        # data source calibrations override
        if self.data_source:
            calibrations = self.data_source.calculated_calibrations
        else:
            calibrations = self.intrinsic_calibrations
        data_shape, data_dtype = self.__get_root_data_shape_and_dtype()
        if data_shape is not None and data_dtype is not None:
            for operation_item in self.operations:
                if operation_item.enabled:
                    calibrations = operation_item.get_processed_calibrations(data_shape, data_dtype, calibrations)
                    data_shape, data_dtype = operation_item.get_processed_data_shape_and_dtype(data_shape, data_dtype)
        return calibrations
    calculated_calibrations = property(__get_calculated_calibrations)

    # date times

    def __get_datetime_original_as_string(self):
        datetime_original = self.datetime_original
        if datetime_original:
            datetime_ = Utility.get_datetime_from_datetime_item(datetime_original)
            if datetime_:
                return datetime_.strftime("%c")
        # fall through to here
        return str()
    datetime_original_as_string = property(__get_datetime_original_as_string)

    # access metadata

    def get_metadata(self, name):
        return copy.deepcopy(self.__metadata.get(name, dict()))

    def open_metadata(self, name):
        metadata = self.__metadata
        metadata_changed = self.__metadata_changed
        class MetadataContextManager(object):
            def __init__(self, data_item, name):
                self.__data_item = data_item
                self.__metadata_copy = data_item.get_metadata(name)
                self.__name = name
            def __enter__(self):
                return self.__metadata_copy
            def __exit__(self, type, value, traceback):
                if self.__metadata_copy is not None:
                    metadata_group = metadata.setdefault(self.__name, dict())
                    metadata_group.clear()
                    metadata_group.update(self.__metadata_copy)
                    self.__data_item.vault.set_value(self.__name, copy.deepcopy(metadata_group))
                    metadata_changed()
        return MetadataContextManager(self, name)

    # access properties

    def __get_properties(self):
        return copy.deepcopy(self.__properties)
    properties = property(__get_properties)

    def __grab_properties(self):
        return self.__properties
    def __release_properties(self):
        self.notify_set_property("properties", self.__properties)

    def property_changes(self):
        grab_properties = DataItem.__grab_properties
        release_properties = DataItem.__release_properties
        class PropertyChangeContextManager(object):
            def __init__(self, data_item):
                self.__data_item = data_item
            def __enter__(self):
                return self
            def __exit__(self, type, value, traceback):
                release_properties(self.__data_item)
            def __get_properties(self):
                return grab_properties(self.__data_item)
            properties = property(__get_properties)
        return PropertyChangeContextManager(self)

    def __insert_display(self, name, before_index, display):
        display.add_ref()
        display.add_listener(self)
        display._set_data_item(self)
        self.notify_data_item_content_changed(set([DISPLAYS]))

    def __remove_display(self, name, index, display):
        self.notify_data_item_content_changed(set([DISPLAYS]))
        display.remove_listener(self)
        display.remove_ref()
        display._set_data_item(None)

    def add_display(self, display):
        self.append_item("displays", display)

    def remove_display(self, display):
        self.remove_item("displays", display)

    # call this when operations change or data souce changes
    # this allows operations to update their default values
    def sync_operations(self):
        data_shape, data_dtype = self.__get_root_data_shape_and_dtype()
        if data_shape is not None and data_dtype is not None:
            for operation in self.operations:
                operation.update_data_shape_and_dtype(data_shape, data_dtype)
                if operation.enabled:
                    data_shape, data_dtype = operation.get_processed_data_shape_and_dtype(data_shape, data_dtype)

    def __insert_operation(self, name, before_index, operation):
        operation.add_ref()
        operation.add_listener(self)
        operation.add_observer(self)
        self.sync_operations()
        self.notify_data_item_content_changed(set([DATA]))
        if self.data_source:
            self.data_source.add_operation_graphics_to_displays(operation.graphics)

    def __remove_operation(self, name, index, operation):
        self.sync_operations()
        self.notify_data_item_content_changed(set([DATA]))
        if self.data_source:
            self.data_source.remove_operation_graphics_from_displays(operation.graphics)
        operation.remove_listener(self)
        operation.remove_observer(self)
        operation.remove_ref()

    def add_operation(self, operation):
        self.append_item("operations", operation)

    def remove_operation(self, operation):
        self.remove_item("operations", operation)

    # this message comes from the operation.
    # by watching for changes to the operations relationship. when an operation
    # is added/removed, this object becomes a listener via add_listener/remove_listener.
    def operation_changed(self, operation):
        self.notify_data_item_content_changed(set([DATA]))

    # this message comes from the operation.
    # it is generated when the user deletes a operation graphic.
    # that informs the display which notifies the graphic which
    # notifies the operation which notifies this data item. ugh.
    def remove_operation_because_graphic_removed(self, operation):
        self.notify_listeners("request_remove_data_item", self)

    # this message is received by other data items using this one as a data source.
    def add_operation_graphics_to_displays(self, operation_graphics):
        for display in self.displays:
            display.add_operation_graphics(operation_graphics)

    # this message is received by other data items using this one as a data source.
    def remove_operation_graphics_from_displays(self, operation_graphics):
        for display in self.displays:
            display.remove_operation_graphics(operation_graphics)

    # connect this item to its data source, if any. the lookup_data_item parameter
    # is a function to look up data items by uuid. this method also establishes the
    # display graphics for this items operations. direct data source is used for testing.
    def connect_data_source(self, lookup_data_item=None, direct_data_source=None):
        assert lookup_data_item or direct_data_source
        data_sources = self.data_sources
        data_source_uuid_str = data_sources.list[0] if len(data_sources.list) == 1 else None
        data_source = lookup_data_item(uuid.UUID(data_source_uuid_str)) if data_source_uuid_str and lookup_data_item else direct_data_source
        self.__set_data_source(data_source)
        if data_source:
            for operation_item in self.operations:
                data_source.add_operation_graphics_to_displays(operation_item.graphics)

    # disconnect this item from its data source. also removes the graphics for this
    # items operations.
    def disconnect_data_source(self):
        data_source = self.data_source
        if data_source:
            for operation_item in self.operations:
                data_source.remove_operation_graphics_from_displays(operation_item.graphics)
        self.__set_data_source(None)

    # override from storage to watch for changes to this data item. notify observers.
    def notify_set_property(self, key, value):
        super(DataItem, self).notify_set_property(key, value)
        self.notify_data_item_content_changed(set([METADATA]))
        for processor in self.__processors.values():
            processor.item_property_changed(key, value)

    # this message comes from the displays.
    def display_changed(self, display):
        self.notify_data_item_content_changed(set([DISPLAYS]))

    # data_item_content_changed comes from data sources to indicate that data
    # has changed. the connection is established in __set_data_source.
    def data_item_content_changed(self, data_source, changes):
        self.sync_intrinsic_spatial_calibrations()
        assert data_source == self.data_source
        # we don't care about display changes to the data source; only data changes.
        if DATA in changes:
            # propogate to listeners
            self.notify_data_item_content_changed(changes)

    # use a property here to correct add_ref/remove_ref
    # also manage connection to data source.
    # data_source is a caching value only. it is not part of the model.
    def __get_data_source(self):
        return self.__data_source
    def __set_data_source(self, data_source):
        assert data_source is None or not self.has_master_data  # can't have master data and data source
        if self.__data_source:
            with self.__data_mutex:
                self.__data_source.remove_listener(self)
                self.__data_source.remove_ref()
                self.__data_source = None
                self.sync_operations()
        if data_source:
            with self.__data_mutex:
                assert isinstance(data_source, DataItem)
                self.__data_source = data_source
                # we will receive data_item_content_changed from data_source
                self.__data_source.add_listener(self)
                self.__data_source.add_ref()
                self.sync_operations()
            self.data_item_content_changed(self.__data_source, set([SOURCE]))
    data_source = property(__get_data_source)

    # add a reference to the given data source
    def add_data_source(self, data_source):
        self.session_id = data_source.session_id
        data_sources = self.data_sources
        assert len(data_sources.list) == 0
        data_sources.list.append(str(data_source.uuid))
        self.data_sources = data_sources

    # remove a reference to the given data source
    def remove_data_source(self, data_source):
        data_sources = self.data_sources
        assert len(data_sources.list) == 1 and data_sources.list[0] == data_source
        del data_sources.list[0]
        self.data_sources = data_sources
        self.session_id = None

    def __get_master_data(self):
        return self.__master_data
    def __set_master_data(self, data):
        with self.data_item_changes():
            assert not self.closed or data is None
            assert (data.shape is not None) if data is not None else True  # cheap way to ensure data is an ndarray
            assert data is None or self.__data_source is None  # can't have master data and data source
            with self.__data_mutex:
                if data is not None:
                    self.set_cached_value("master_data_shape", data.shape)
                    self.set_cached_value("master_data_dtype", data.dtype)
                else:
                    self.remove_cached_value("master_data_shape")
                    self.remove_cached_value("master_data_dtype")
                self.__master_data = data
                self.__master_data_shape = data.shape if data is not None else None
                self.__master_data_dtype = data.dtype if data is not None else None
                self.__has_master_data = data is not None
                self.sync_intrinsic_spatial_calibrations()
            data_file_path = DataItem._get_data_file_path(self.uuid, self.datetime_original, session_id=self.session_id)
            file_datetime = Utility.get_datetime_from_datetime_item(self.datetime_original)
            # tell the database about it
            if self.__master_data is not None:
                # save these here so that if the data isn't immediately written out, these values can be returned
                # from _get_master_data_data_reference when the data is written.
                self.__master_data_reference_type = "relative_file"
                self.__master_data_reference = data_file_path
                self.__master_data_file_datetime = file_datetime
                self.notify_set_data_reference("master_data", self.__master_data, self.__master_data.shape, self.__master_data.dtype, "relative_file", data_file_path, file_datetime)
                self.notify_set_property("data_range", self.data_range)
            self.notify_data_item_content_changed(set([DATA]))

    # accessor for storage subsystem.
    def _get_master_data_data_reference(self):
        reference_type = self.__master_data_reference_type # if self.__master_data_reference_type else "relative_file"
        reference = self.__master_data_reference # if self.__master_data_reference else DataItem._get_data_file_path(self.uuid, self.datetime_original, session_id=self.session_id)
        file_datetime = self.__master_data_file_datetime # if self.__master_data_file_datetime else Utility.get_datetime_from_datetime_item(self.datetime_original)
        # when data items are initially created, they will have their data in memory.
        # this method will be called when the data gets written out to disk.
        # to ensure that the data gets unloaded, grab it here and release it.
        # if no other object is holding a reference, the data will be unloaded from memory.
        if self.__master_data is not None:
            with self.data_ref() as d:
                master_data = d.master_data
        else:
            master_data = None
        self.master_data_save_event.set()
        return master_data, self.__master_data_shape, self.__master_data_dtype, reference_type, reference, file_datetime

    def set_external_master_data(self, data_file_path, data_shape, data_dtype):
        with self.__data_mutex:
            self.set_cached_value("master_data_shape", data_shape)
            self.set_cached_value("master_data_dtype", data_dtype)
            self.__master_data_shape = data_shape
            self.__master_data_dtype = data_dtype
            self.__has_master_data = True
            self.sync_intrinsic_spatial_calibrations()
            file_datetime = datetime.datetime.fromtimestamp(os.path.getmtime(data_file_path))
        # save these here so that if the data isn't immediately written out, these values can be returned
        # from _get_master_data_data_reference when the data is written.
        self.__master_data_reference_type = "external_file"
        self.__master_data_reference = data_file_path
        self.__master_data_file_datetime = file_datetime
        self.notify_set_data_reference("master_data", None, data_shape, data_dtype, "external_file", data_file_path, file_datetime)
        self.notify_set_property("data_range", self.data_range)
        self.notify_data_item_content_changed(set([DATA]))

    def __load_master_data(self):
        # load data from datastore if not present
        if self.has_master_data and self.datastore and self.__master_data is None:
            #logging.debug("loading %s", self)
            reference_type, reference = self.datastore.get_data_reference(self.datastore.find_parent_node(self), "master_data")
            self.__master_data = self.datastore.load_data_reference("master_data", reference_type, reference)

    def __unload_master_data(self):
        # unload data if it can be reloaded from datastore.
        # data cannot be unloaded if transaction count > 0 or if there is no datastore.
        if self.transaction_count == 0 and self.has_master_data and self.datastore:
            self.__master_data = None
            self.__cached_data = None
            #logging.debug("unloading %s", self)

    def increment_data_ref_count(self):
        with self.__data_ref_count_mutex:
            initial_count = self.__data_ref_count
            self.__data_ref_count += 1
            if initial_count == 0:
                if self.__data_source:
                    self.__data_source.increment_data_ref_count()
                else:
                    self.__load_master_data()
        return initial_count+1
    def decrement_data_ref_count(self):
        with self.__data_ref_count_mutex:
            self.__data_ref_count -= 1
            final_count = self.__data_ref_count
            if final_count == 0:
                if self.__data_source:
                    self.__data_source.decrement_data_ref_count()
                else:
                    self.__unload_master_data()
        return final_count

    # used for testing
    def __is_data_loaded(self):
        return self.has_master_data and self.__master_data is not None
    is_data_loaded = property(__is_data_loaded)

    def __get_has_master_data(self):
        return self.__has_master_data
    has_master_data = property(__get_has_master_data)

    def __get_has_data_source(self):
        return self.__data_source is not None
    has_data_source = property(__get_has_data_source)

    # grab a data reference as a context manager. the object
    # returned defines data and master_data properties. reading data
    # should use the data property. writing data (if allowed) should
    # assign to the master_data property.
    def data_ref(self):
        get_master_data = DataItem.__get_master_data
        set_master_data = DataItem.__set_master_data
        get_data = DataItem.__get_data
        class DataAccessor(object):
            def __init__(self, data_item):
                self.__data_item = data_item
            def __enter__(self):
                self.__data_item.increment_data_ref_count()
                return self
            def __exit__(self, type, value, traceback):
                self.__data_item.decrement_data_ref_count()
            def __get_master_data(self):
                return get_master_data(self.__data_item)
            def __set_master_data(self, data):
                set_master_data(self.__data_item, data)
            master_data = property(__get_master_data, __set_master_data)
            def master_data_updated(self):
                pass
            def __get_data(self):
                return get_data(self.__data_item)
            data = property(__get_data)
        return DataAccessor(self)

    def __get_data_immediate(self):
        """ add_ref, get data, remove_ref """
        with self.data_ref() as data_ref:
            return data_ref.data
    data = property(__get_data_immediate)

    # get the root data shape and dtype without causing calculation to occur if possible.
    def __get_root_data_shape_and_dtype(self):
        with self.__data_mutex:
            if self.has_master_data:
                return self.__master_data_shape, self.__master_data_dtype
            if self.has_data_source:
                return self.data_source.data_shape_and_dtype
        return None, None

    def __clear_cached_data(self):
        with self.__data_mutex:
            self.__cached_data_dirty = True
            self.set_cached_value_dirty("data_range")

    # data property. read only. this method should almost *never* be called on the main thread since
    # it takes an unpredictable amount of time.
    def __get_data(self):
        if threading.current_thread().getName() == "MainThread":
            #logging.debug("*** WARNING: data called on main thread ***")
            #import traceback
            #traceback.print_stack()
            pass
        self.__data_mutex.acquire()
        if self.__cached_data_dirty or self.__cached_data is None:
            self.__data_mutex.release()
            with self.__get_data_mutex:
                # this should NOT happen under the data mutex. it can take a long time.
                data = None
                if self.has_master_data:
                    data = self.__master_data
                if data is None:
                    if self.data_source:
                        with self.data_source.data_ref() as data_ref:
                            # this can be a lengthy operation
                            data = data_ref.data
                operations = self.operations
                if len(operations) and data is not None:
                    # apply operations
                    if data is not None:
                        for operation in reversed(operations):
                            data = operation.process_data(data)
                self.__get_data_range_for_data(data)
            with self.__data_mutex:
                self.__cached_data = data
                self.__cached_data_dirty = False
        else:
            self.__data_mutex.release()
        return self.__cached_data

    def __get_data_shape_and_dtype(self):
        with self.__data_mutex:
            if self.has_master_data:
                data_shape = self.__master_data_shape
                data_dtype = self.__master_data_dtype
            elif self.has_data_source:
                data_shape = self.data_source.data_shape
                data_dtype = self.data_source.data_dtype
            else:
                data_shape = None
                data_dtype = None
            # apply operations
            if data_shape is not None:
                for operation in self.operations:
                    if operation.enabled:
                        data_shape, data_dtype = operation.get_processed_data_shape_and_dtype(data_shape, data_dtype)
            return data_shape, data_dtype
    data_shape_and_dtype = property(__get_data_shape_and_dtype)

    def __get_size_and_data_format_as_string(self):
        spatial_shape = self.spatial_shape
        data_dtype = self.data_dtype
        if spatial_shape is not None and data_dtype is not None:
            spatial_shape_str = " x ".join([str(d) for d in spatial_shape])
            if len(spatial_shape) == 1:
                spatial_shape_str += " x 1"
            dtype_names = {
                numpy.int8: _("Integer (8-bit)"),
                numpy.int16: _("Integer (16-bit)"),
                numpy.int32: _("Integer (32-bit)"),
                numpy.int64: _("Integer (64-bit)"),
                numpy.uint8: _("Unsigned Integer (8-bit)"),
                numpy.uint16: _("Unsigned Integer (16-bit)"),
                numpy.uint32: _("Unsigned Integer (32-bit)"),
                numpy.uint64: _("Unsigned Integer (64-bit)"),
                numpy.float32: _("Real (32-bit)"),
                numpy.float64: _("Real (64-bit)"),
                numpy.complex64: _("Complex (2 x 32-bit)"),
                numpy.complex128: _("Complex (2 x 64-bit)"),
            }
            if self.is_data_rgb_type:
                data_size_and_data_format_as_string = _("RGB (8-bit)") if self.is_data_rgb else _("RGBA (8-bit)")
            else:
                if not self.data_dtype.type in dtype_names:
                    logging.debug("Unknown %s", self.data_dtype)
                data_size_and_data_format_as_string = dtype_names[self.data_dtype.type] if self.data_dtype.type in dtype_names else _("Unknown Data Type")
            return "{0}, {1}".format(spatial_shape_str, data_size_and_data_format_as_string)
        return _("No Data")
    size_and_data_format_as_string = property(__get_size_and_data_format_as_string)

    def __get_data_shape(self):
        return self.data_shape_and_dtype[0]
    data_shape = property(__get_data_shape)

    def __get_spatial_shape(self):
        data_shape, data_dtype = self.data_shape_and_dtype
        return Image.spatial_shape_from_shape_and_dtype(data_shape, data_dtype)
    spatial_shape = property(__get_spatial_shape)

    def __get_data_dtype(self):
        return self.data_shape_and_dtype[1]
    data_dtype = property(__get_data_dtype)

    def __is_data_1d(self):
        data_shape, data_dtype = self.data_shape_and_dtype
        return Image.is_shape_and_dtype_1d(data_shape, data_dtype)
    is_data_1d = property(__is_data_1d)

    def __is_data_2d(self):
        data_shape, data_dtype = self.data_shape_and_dtype
        return Image.is_shape_and_dtype_2d(data_shape, data_dtype)
    is_data_2d = property(__is_data_2d)

    def __is_data_3d(self):
        data_shape, data_dtype = self.data_shape_and_dtype
        return Image.is_shape_and_dtype_3d(data_shape, data_dtype)
    is_data_3d = property(__is_data_3d)

    def __is_data_rgb(self):
        data_shape, data_dtype = self.data_shape_and_dtype
        return Image.is_shape_and_dtype_rgb(data_shape, data_dtype)
    is_data_rgb = property(__is_data_rgb)

    def __is_data_rgba(self):
        data_shape, data_dtype = self.data_shape_and_dtype
        return Image.is_shape_and_dtype_rgba(data_shape, data_dtype)
    is_data_rgba = property(__is_data_rgba)

    def __is_data_rgb_type(self):
        data_shape, data_dtype = self.data_shape_and_dtype
        return Image.is_shape_and_dtype_rgb(data_shape, data_dtype) or Image.is_shape_and_dtype_rgba(data_shape, data_dtype)
    is_data_rgb_type = property(__is_data_rgb_type)

    def __is_data_scalar_type(self):
        data_shape, data_dtype = self.data_shape_and_dtype
        return Image.is_shape_and_dtype_scalar_type(data_shape, data_dtype)
    is_data_scalar_type = property(__is_data_scalar_type)

    def __is_data_complex_type(self):
        data_shape, data_dtype = self.data_shape_and_dtype
        return Image.is_shape_and_dtype_complex_type(data_shape, data_dtype)
    is_data_complex_type = property(__is_data_complex_type)

    def get_data_value(self, pos):
        # do not force data calculation here, but trigger data loading
        if self.__cached_data is None:
            pass  # TODO: Cursor should trigger loading of data if not already laoded.
        with self.__data_mutex:
            if self.is_data_1d:
                if self.__cached_data is not None:
                    return self.__cached_data[pos[0]]
            elif self.is_data_2d:
                if self.__cached_data is not None:
                    return self.__cached_data[pos[0], pos[1]]
            # TODO: fix me 3d
            elif self.is_data_3d:
                if self.__cached_data is not None:
                    return self.__cached_data[pos[0], pos[1]]
        return None


_computation_fns = list()

def register_data_item_computation(computation_fn):
    global _computation_fns
    _computation_fns.append(computation_fn)

def unregister_data_item_computation(self, computation_fn):
    pass
