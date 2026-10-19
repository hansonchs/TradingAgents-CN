"""Shared private research ownership checks for Mongo and memory records."""
from bson import ObjectId
from fastapi import HTTPException


def owned_query(query, user):
    if user.get("is_admin") is True:
        return query
    owner = user.get("id") or user.get("_id")
    if owner is None or not str(owner):
        raise HTTPException(status_code=401, detail="缺少用户身份")
    owners = [str(owner)]
    if ObjectId.is_valid(str(owner)):
        owners.append(ObjectId(str(owner)))
    scope = {"user_id": {"$in": owners}}
    return {"$and": [query, scope]} if query else scope


def require_owner(record, user):
    if user.get("is_admin") is True:
        return
    owner = user.get("id") or user.get("_id")
    if not owner or record.get("user_id") is None or str(record["user_id"]) != str(owner):
        raise HTTPException(status_code=404, detail="任务不存在")
