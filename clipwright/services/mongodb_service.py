#! python3
# -*- coding: utf-8 -*-
"""
This file defines the parent class for all the MongoDB Models.

@File   : mongodb_service.py
@Created: 2025/04/09 16:17
@Author : Zhong, Yinjie
@Email  : yinjie.zhong@outlook.com
"""

from __future__ import annotations
from abc import ABC
from bson import ObjectId
from datetime import datetime
from typing import Any, Dict, List, Tuple
from pymongo.errors import OperationFailure
import uuid

from ..context import mongo
from ..config import TIME_ZONE, MONGO_TRANSACTIONS_ENABLED


def _io(fn, *args, **kwargs):
    """在 async 上下文把同步 pymongo 调用 offload 到线程池，sync 上下文则直接执行。

    pymongo 是同步驱动；在 ``async def`` 路由（事件循环线程）里直接调用会冻住整个服务。
    本帮手让 model 方法对 sync/async 调用方都透明：async 下走 ``asyncio.to_thread``，
    sync 下（后台 worker 等）原样执行。pymongo 的 MongoClient 本身线程安全，
    各请求使用独立 session/游标，故并发 offload 安全。
    """
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return fn(*args, **kwargs)
    return asyncio.to_thread(fn, *args, **kwargs)


class MongoDbModel(ABC):
    """Abstract base model class with common MongoDB operations using PyMongo."""

    collection_name: str = None  # Optional explicit collection name

    _supports_transactions = MONGO_TRANSACTIONS_ENABLED

    @classmethod
    def init_transaction_support(cls):
        """
        Initializes the transaction support status at application startup.
        This avoids detection overhead and potential double execution during runtime.
        """
        if cls._supports_transactions is not None:
            return cls._supports_transactions

        try:
            # Check topology to determine transaction support
            if mongo.cx.topology_description.topology_type_name == "Unknown":
                # Trigger connection to update topology info
                mongo.cx.admin.command('ping')
            
            topology = mongo.cx.topology_description.topology_type_name
            if topology == "Single":
                cls._supports_transactions = False
            else:
                # ReplicaSetWithPrimary or Sharded or others that might support transactions
                cls._supports_transactions = True
        except Exception:
            # If we can't determine, assume support and let the first failure catch it
            cls._supports_transactions = True
        
        return cls._supports_transactions

    def __init__(self, created_time: datetime = None, updated_time: datetime = None, **kwargs):
        """Initializes instance attributes from a dictionary.

        Args:
            **kwargs: Additional attributes to assign to the instance.
        """
        if (created_time):
            self.created_time = created_time.astimezone(TIME_ZONE)
        if (updated_time):
            self.updated_time = updated_time.astimezone(TIME_ZONE)

        # Ensure `_id` is set to a UUID string
        if "_id" not in kwargs:
            kwargs["_id"] = str(uuid.uuid4())

        for key, value in kwargs.items():
            if not hasattr(self, key):
                setattr(self, key, value)
        return

    def to_dict(self, to_database: bool = False) -> dict:
        """Serializes this instance into a dictionary.

        Args:
            to_database (bool, optional): Indicate if the instance is to be saved to the database. Default False.

        Returns:
            dict: The instance's data as a dictionary.
        """
        data = self.__dict__.copy()

        if not to_database:
            data["id"] = self.id
            del data["_id"]

        if not to_database:
            for key, value in data.items():
                if isinstance(value, datetime):
                    data[key] = value.astimezone(TIME_ZONE).isoformat()
        return data

    def to_legacy_dict(self) -> dict:
        """
        Serializes this instance into a dictionary and converts datetime objects
        to Unix integer timestamps for legacy frontend compatibility.
        """
        data = self.to_dict()
        for field in ["created_time", "updated_time", "created_at", "updated_at", "timestamp"]:
            if hasattr(self, field) and isinstance(getattr(self, field), datetime):
                attr_value = getattr(self, field)
                ts = int(attr_value.timestamp())
                if field == "created_time": 
                    data["created_at"] = ts
                    data["timestamp"] = ts
                elif field == "updated_time":
                    data["updated_at"] = ts
                else:
                    data[field] = ts
        return data

    @classmethod
    def from_dict(cls, data: dict):
        """Deserializes a dictionary into a model instance.

        Args:
            data (dict): The source dictionary.

        Returns:
            MongoDbModel: An instance of the class.
        """
        # Attempt to convert datetime fields from string to datetime
        for key, value in data.items():
            if isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value)
                    data[key] = parsed
                except ValueError:
                    pass  # Keep original if not ISO 8601 datetime
        
        instance = cls(**data)
        if "_id" in data:
            setattr(instance, "_id", data["_id"])
        return instance
    
    @property
    def id(self) -> str:
        """Returns the string version of the document's `_id`.

        Returns:
            str: The document's `_id`.
        """
        return str(self._id)
    
    @id.setter
    def id(self, value):
        """Set the document's `_id`."""
        self._id = value
        return

    @classmethod
    def _derive_collection_name(cls) -> str:
        """Derives a collection name from the class name.

        Returns:
            str: The pluralized lowercase form of the class name.
        """
        name = cls.__name__.lower()
        return name if name.endswith("s") else name + "s"

    @classmethod
    def get_collection(cls):
        """Gets the MongoDB collection object.

        Returns:
            Collection: The PyMongo collection for this model.
        """
        name = cls.collection_name or cls._derive_collection_name()
        return mongo.db[name]

    @classmethod
    def create_index(cls, keys, **kwargs):
        """Creates an index on the collection.

        Args:
            keys (Any): Index specification.
            **kwargs: Additional options for index creation.

        Returns:
            str: The name of the created index.
        """
        return cls.get_collection().create_index(keys, **kwargs)

    @classmethod
    def list_indexes(cls):
        """Lists all indexes on the collection.

        Returns:
            CommandCursor: A cursor over the index documents.
        """
        return cls.get_collection().list_indexes()

    @classmethod
    def drop_index(cls, index_name: str):
        """Drops an index by name.

        Args:
            index_name (str): The name of the index to drop.
        """
        return cls.get_collection().drop_index(index_name)

    @classmethod
    def find_by_id(cls, id):
        """Finds a document by its ObjectId.

        Args:
            id (str or ObjectId): The ID of the document.

        Returns:
            MongoDbModel or None: The matching document as a model instance.
        """
        try:
            oid = ObjectId(id) if not isinstance(id, ObjectId) else id
            data = _io(cls.get_collection().find_one, {"_id": oid})
        except Exception:
            # ObjectId 转换失败（如 UUID 格式），按字符串 _id 查找
            data = _io(cls.get_collection().find_one, {"_id": id})
        return cls.from_dict(data) if data else None

    @classmethod
    def find_one(cls, filter: dict = {}, sort: List[Tuple[str, int]] = []):
        """Finds a single document by filter.

        Args:
            filter (dict): A MongoDB filter query.
            sort (List[Tuple[str, int]], optional): Sort order.

        Returns:
            MongoDbModel or None: The matching document as a model instance.
        """
        def _run():
            cursor = cls.get_collection().find(filter or {})
            if sort:
                cursor = cursor.sort(sort)
            data = cursor.limit(1)
            try:
                return next(data)
            except StopIteration:
                return None
        doc = _io(_run)
        return cls.from_dict(doc) if doc else None

    @classmethod
    def find_many(cls, filter: dict = {}, sort: List[Tuple[str, int]] = [], skip: int = 0, limit: int = 0):
        """Finds multiple documents by filter.

        Args:
            filter (dict, optional): A MongoDB filter query. Defaults to {}.
            sort (List[Tuple[str, int]], optional): A list of (key, direction) pairs.
            skip (int, optional): Number of documents to skip.
            limit (int, optional): Maximum number of documents to return.

        Returns:
            list: A list of model instances.
        """
        def _run():
            cursor = cls.get_collection().find(filter or {})
            if sort:
                cursor = cursor.sort(sort)
            if skip:
                cursor = cursor.skip(skip)
            if limit:
                cursor = cursor.limit(limit)
            return [cls.from_dict(doc) for doc in cursor]
        return _io(_run)

    @classmethod
    def count(cls, filter: dict = {}):
        """Count multiple documents by filter.

        Args:
            filter (dict, optional): A MongoDB filter query. Defaults to {}.

        Returns:
            list: A list of model instances.
        """
        document_count = _io(cls.get_collection().count_documents, filter or {})
        return document_count

    @classmethod
    def aggregate(cls, pipeline: List[Dict]) -> list:
        """Performs an aggregation query on the model's collection.

        Args:
            pipeline (List[Dict]): A list of aggregation pipeline stages.

        Returns:
            list: The result of the aggregation pipeline.
        """
        return _io(lambda: list(cls.get_collection().aggregate(pipeline)))

    def insert(self, session=None):
        """Inserts this instance as a new document in the database.

        Args:
            session (ClientSession, optional): A MongoDB session for transactions.

        Returns:
            inserted_id (ObjectId): The generated ObjectId of the inserted document.
        """
        self.created_time = datetime.now(tz=TIME_ZONE)
        self.updated_time = self.created_time
        data = self.to_dict(to_database=True)
        custom_id = data.pop("_id", None)
        data.pop("id", None)
        if custom_id:
            data["_id"] = custom_id
        result = _io(self.get_collection().insert_one, data, session=session)
        if not custom_id:
            self._id = result.inserted_id
        return result.inserted_id

    def update(self, session=None) -> int:
        """Updates the document corresponding to this instance.

        Args:
            session (ClientSession, optional): A MongoDB session for transactions.

        Returns:
            modified_count (int): The number of documents modified (0 or 1).
        """
        if not hasattr(self, "_id"):
            raise ValueError("This instance must have _id to update.")
        self.updated_time = datetime.now(tz=TIME_ZONE)
        data = self.to_dict(to_database=True)
        data.pop("_id", None)
        result = _io(self.get_collection().update_one, {"_id": self._id}, {"$set": data}, session=session)
        return result.modified_count

    def delete(self, session=None) -> int:
        """Deletes the document corresponding to this instance.

        Args:
            session (ClientSession, optional): A MongoDB session for transactions.

        Returns:
            deleted_count (int): The number of documents deleted (0 or 1).
        """
        if not hasattr(self, "_id"):
            raise ValueError("This instance must have _id to delete.")
        result = _io(self.get_collection().delete_one, {"_id": self._id}, session=session)
        return result.deleted_count

    def unset_attributes(
        self,
        field_names: List[str],
        protected_fields: List[str] = None
    ) -> int:
        """
        Unsets (deletes) one or more fields from the MongoDB document
        corresponding to this instance.

        This method will not delete protected fields. By default, '_id' is
        always protected. The operation is atomic at the database level.
        The attributes are also removed from the in-memory model instance.

        Args:
            field_names (List[str]): A list of field names to delete from the document.
            protected_fields (List[str], optional): An additional list of fields
                that should not be deleted. Defaults to None.

        Returns:
            int: The number of documents modified (0 or 1).

        Raises:
            ValueError: If the instance does not have an `_id` attribute.
        """
        if not hasattr(self, "_id"):
            raise ValueError("This instance must have _id to unset attributes.")

        # Define core fields that can never be unset.
        core_protected = ["_id"]
        
        # Combine default and user-provided protected fields.
        all_protected = set(core_protected)
        if protected_fields:
            all_protected.update(protected_fields)

        # Filter out any protected fields from the list of fields to unset.
        fields_to_actually_unset = [
            f for f in field_names if f not in all_protected
        ]

        if not fields_to_actually_unset:
            return 0  # Nothing to do

        # Construct the $unset payload for MongoDB.
        unset_payload = {"$unset": {field: "" for field in fields_to_actually_unset}}

        # Perform the database operation.
        result = _io(
            self.get_collection().update_one,
            {"_id": self._id}, unset_payload
        )

        # If successful, also remove the attributes from the in-memory instance.
        if result.modified_count > 0:
            for field in fields_to_actually_unset:
                if hasattr(self, field):
                    delattr(self, field)

        return result.modified_count

    def unset_attribute(
        self,
        field_name: str,
        protected_fields: List[str] = None
    ) -> int:
        """
        Unsets (deletes) a single field from the MongoDB document.

        This is a convenience wrapper around `unset_attributes`.

        Args:
            field_name (str): The name of the field to delete.
            protected_fields (List[str], optional): An additional list of fields
                that should not be deleted.

        Returns:
            int: The number of documents modified (0 or 1).
        """
        return self.unset_attributes([field_name], protected_fields=protected_fields)

    @classmethod
    def execute_atomic(cls, callback, **kwargs):
        """
        Executes a callback function within a MongoDB transaction if supported.
        
        Args:
            callback (callable): A function that accepts a `session` keyword argument.
            **kwargs: Additional arguments to pass to the callback.
            
        Returns:
            The return value of the callback.
        """
        # If initialization wasn't called, do it once here (lazy fallback)
        if cls._supports_transactions is None:
            cls.init_transaction_support()

        def _run():
            if cls._supports_transactions is False:
                return callback(session=None, **kwargs)
            try:
                with mongo.cx.start_session() as session:
                    with session.start_transaction():
                        return callback(session=session, **kwargs)
            except OperationFailure as e:
                # Code 20: Transaction numbers are only allowed on a replica set member or mongos
                if e.code == 20:
                    cls._supports_transactions = False
                    return callback(session=None, **kwargs)
                raise e

        return _io(_run)

    @classmethod
    def delete_many(cls, filter: dict, session=None) -> int:
        """Deletes multiple documents matching the filter.

        The filter must follow the standard MongoDB query syntax. For example,
        to delete all documents where the 'status' field is 'inactive',
        the filter would be `{"status": "inactive"}`.

        Args:
            filter (dict): A MongoDB filter query document. An empty dictionary
                           is not permitted, to prevent accidental deletion of
                           all documents in a collection.
            session (ClientSession, optional): A MongoDB session for transactions.

        Returns:
            int: The number of documents deleted.
        """
        if not isinstance(filter, dict) or not filter:
            raise ValueError("A non-empty filter dictionary is required for delete_many to prevent accidental mass deletion.")
        
        result = _io(cls.get_collection().delete_many, filter, session=session)
        return result.deleted_count

    def update_attributes(
        self,
        mapper: Dict[str, Any],
        editable_attrs: List[str] = list(),
        update_timestamp: bool = True,
        timestamp_field: str = "updated_time",
        skip_none_or_empty: bool = False,
        add_nonexistent_attrs: bool = False,
        session=None
    ) -> Tuple[List[str], List[str], List[str], str]:
        """Updates selected fields of this instance and syncs with MongoDB.

        Args:
            mapper (Dict[str, Any]): Dictionary of field names and new values.
            editable_attrs (List[str], optional): List of allowed fields to edit.
            update_timestamp (bool, optional): Whether to update a timestamp field.
            timestamp_field (str, optional): Name of the timestamp field.
            skip_none_or_empty (bool, optional): Indicate if None or Empty value in the `mapper` should be skipped. Default False.
            add_nonexistent_attrs (bool, optional): Indicate if nonexistent attrs can be declared. Default False.
            session (ClientSession, optional): A MongoDB session for transactions.

        Returns:
            Tuple[List[str], List[str], List[str], str]: Updated fields, blocked,
            nonexistent, and message summary.
        """
        updated_attrs = []
        blocked_attrs = []
        nonexistent_attrs = []
        update_data = {}

        for field_name, field_value in mapper.items():
            if (skip_none_or_empty and (not field_name)):
                continue
            has_attr = hasattr(self, field_name)
            if has_attr or add_nonexistent_attrs:
            # If to move on with this field.
                if (field_name in editable_attrs) or (not editable_attrs):
                # If this field can be updated.
                    if (add_nonexistent_attrs or (has_attr and getattr(self, field_name) != field_value)):
                    # If this field needs to be updated.
                        if (field_value or (not skip_none_or_empty)):
                        # If the value worth to update.
                            setattr(self, field_name, field_value)
                            update_data[field_name] = field_value
                else:
                    blocked_attrs.append(field_name)
            else:
                nonexistent_attrs.append(field_name)

        if update_data:
            if update_timestamp:
                now = datetime.now(TIME_ZONE)
                setattr(self, timestamp_field, now)
                update_data[timestamp_field] = now

            try:
                result = _io(
                    self.get_collection().update_one,
                    {"_id": self._id}, {"$set": update_data}, session=session
                )
                if result.modified_count > 0:
                    updated_attrs = list(update_data.keys())
            except Exception as e:
                raise RuntimeError(f"Update failed: {e}") from e

        message_parts = []
        if updated_attrs:
            message_parts.append(f"Updated: {', '.join(updated_attrs)}.")
        if blocked_attrs:
            message_parts.append(f"Blocked: {', '.join(blocked_attrs)}.")
        if nonexistent_attrs:
            message_parts.append(f"Nonexistent: {', '.join(nonexistent_attrs)}.")
        return updated_attrs, blocked_attrs, nonexistent_attrs, " ".join(message_parts)

    def inc_attributes(
        self,
        mapper: Dict[str, int],
        session=None
    ) -> int:
        """
        Atomically increment one or more fields.
        
        Args:
            mapper (Dict[str, int]): Dictionary of field names and increment values (can be negative).
            session (ClientSession, optional): A MongoDB session for transactions.
            
        Returns:
            int: The number of documents modified (0 or 1).
        """
        if not hasattr(self, "_id"):
            raise ValueError("This instance must have _id to increment attributes.")
            
        if not mapper:
            return 0
            
        # Filter out fields that don't exist in the object (optional safety check, 
        # but Mongo $inc creates fields if missing. We might want to restrict this?)
        # For now, let's allow Mongo to handle it, but we should update the in-memory object too.
        
        try:
            result = _io(
                self.get_collection().update_one,
                {"_id": self._id},
                {"$inc": mapper},
                session=session
            )
            
            if result.modified_count > 0:
                for key, value in mapper.items():
                    # Update in-memory object
                    current_val = getattr(self, key, 0)
                    # Ensure current_val is a number before adding
                    if not isinstance(current_val, (int, float)):
                        current_val = 0
                    setattr(self, key, current_val + value)
            
            return result.modified_count
        except Exception as e:
            raise RuntimeError(f"Increment failed: {e}") from e

    def synchronize_from(self, source_obj: MongoDbModel, 
            synchronize_attributes: List[str] = list(), 
            skip_none_or_empty: bool = False
        ) -> int:
        """Synchronize specific attributes from another profile to current.

        Args:
            source_obj (MongoDbModel): The object to synchronize from.
            synchronize_attributes (List[str], optional): The attributes to be synchronized.
            skip_none_or_empty (bool, optional): Indicate if None or Empty value should be skipped.

        Returns:
            modified_count (int): The number of documents modified.
        """
        modified_count = 0
        attr_mapper = dict()
        for attr_key in synchronize_attributes:
            if (hasattr(self, attr_key)):
                attr_mapper[attr_key] = getattr(source_obj, attr_key)
            continue
        updated_fields, _, _, _ = self.update_attributes(mapper=attr_mapper, skip_none_or_empty=skip_none_or_empty)
        if (updated_fields):
            modified_count += 1
        return modified_count
