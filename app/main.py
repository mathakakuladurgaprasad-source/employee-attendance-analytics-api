

"""

Employee Attendance & Analytics API.

Run: uvicorn app.main:app --port 8000

Configuration: MONGO_URI and MONGO_DB environment variables.

"""



import os

import re

import math

import json

from bson import json_util



from bson import json_util

from datetime import datetime, date, time, timedelta, timezone

from decimal import Decimal, ROUND_HALF_UP

from typing import Optional, Literal, Any



from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Query, Path as ApiPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument

from pymongo.errors import DuplicateKeyError, PyMongoError



load_dotenv()



MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

MONGO_DB = os.getenv("MONGO_DB", "attendance_db")



client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2500)

db = client[MONGO_DB]

employees = db.employees

logs = db.attendance_logs



IST = timezone(timedelta(hours=5, minutes=30))

UTC = timezone.utc



PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}

ALL_STATUS = PRESENCE | {"ABSENT", "LEAVE"}



EMP_RE = re.compile(r"^EMP\d{4,6}$")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")



MIN_TS = 100_000_000_000

MAX_TS = 4_102_444_800_000



app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")





def now_utc() -> datetime:

    return datetime.now(UTC).replace(microsecond=0)





def epoch_ms(value: datetime | None) -> int | None:

    if value is None:

        return None

    if value.tzinfo is None:

        value = value.replace(tzinfo=UTC)

    return int(value.timestamp() * 1000)





def from_epoch_ms(value: int) -> datetime:

    # R1: truncate the instant to whole seconds before calculations/storage.

    return datetime.fromtimestamp(value // 1000, tz=UTC).replace(microsecond=0)





def parse_date(value: str) -> date:

    try:

        return date.fromisoformat(value)

    except (TypeError, ValueError):

        raise HTTPException(422, "Date must be a valid YYYY-MM-DD date")





def parse_month(value: str) -> tuple[date, date]:

    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value or ""):

        raise HTTPException(422, "month must use YYYY-MM format")

    year, month = map(int, value.split("-"))

    first = date(year, month, 1)

    last = date(

        year + (month == 12),

        1 if month == 12 else month + 1,

        1,

    ) - timedelta(days=1)

    return first, last





def date_string(d: date) -> str:

    return d.isoformat()





def dt_utc(value: datetime | None) -> datetime | None:

    if value is None:

        return None

    if value.tzinfo is None:

        return value.replace(tzinfo=UTC).replace(microsecond=0)

    return value.astimezone(UTC).replace(microsecond=0)





def shift_time(value: str) -> time:

    if not TIME_RE.fullmatch(value or ""):

        raise HTTPException(422, "Shift times must be valid HH:MM values")

    h, m = map(int, value.split(":"))

    return time(h, m)





def round_half_up(value: float | Decimal, places: int = 2) -> float:

    q = Decimal("1").scaleb(-places)

    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))





def presence_value(doc: dict) -> float:

    if doc.get("status") not in PRESENCE:

        return 0.0

    return 0.5 if doc.get("half_day", False) else 1.0





def attendance_date(

    punch_in: datetime,

    shift_start: str,

    shift_end: str,

) -> date:

    local = punch_in.astimezone(IST)

    start, end = shift_time(shift_start), shift_time(shift_end)



    # Overnight shifts: times earlier than shift_end belong to the previous day.

    if end <= start and local.time().replace(tzinfo=None) < end:

        return local.date() - timedelta(days=1)

    return local.date()





def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:

    local = punch_in.astimezone(IST)

    start = shift_time(shift_start)

    scheduled = datetime.combine(local.date(), start, tzinfo=IST)

    seconds = int((local - scheduled).total_seconds())

    return seconds // 60 if seconds > 600 else 0





def shift_end_datetime(

    attendance_day: str,

    shift_start: str,

    shift_end: str,

) -> datetime:

    day = parse_date(attendance_day)

    start, end = shift_time(shift_start), shift_time(shift_end)

    if end <= start:

        day += timedelta(days=1)

    return datetime.combine(day, end, tzinfo=IST)





def compute_overtime(

    punch_out: datetime | None,

    attendance_day: str,

    shift_start: str,

    shift_end: str,

) -> int:

    if punch_out is None:

        return 0

    end = shift_end_datetime(attendance_day, shift_start, shift_end)

    seconds = int((punch_out.astimezone(IST) - end).total_seconds())

    minutes = max(0, seconds // 60)

    return minutes if minutes >= 30 else 0





def compute_work_hours(

    punch_in: datetime | None,

    punch_out: datetime | None,

) -> float | None:

    if punch_in is None or punch_out is None:

        return None

    seconds = (punch_out - punch_in).total_seconds()

    return round_half_up(Decimal(str(seconds)) / Decimal(3600), 2)





def derive_fields(doc: dict, emp: dict) -> dict:

    pin = dt_utc(doc.get("punch_in"))

    pout = dt_utc(doc.get("punch_out"))

    status = doc.get("status")



    if status in {"ABSENT", "LEAVE"}:

        pin = pout = None



    late = (

        compute_late_minutes(pin, emp["shift_start"])

        if pin and status in PRESENCE else 0

    )

    work = (

        compute_work_hours(pin, pout)

        if pin and pout and status in PRESENCE else None

    )

    overtime = (

        compute_overtime(pout, doc["date"], emp["shift_start"], emp["shift_end"])

        if pout and status in PRESENCE else 0

    )

    half = bool(work is not None and work < 4.50)



    return {

        "punch_in": pin,

        "punch_out": pout,

        "late_minutes": late,

        "work_hours": work,

        "overtime_minutes": overtime,

        "half_day": half,

    }





def output_doc(doc: dict) -> dict:

    # HTTP contract exposes neither Mongo _id nor synthetic id.

    out = {k: v for k, v in doc.items() if k not in {"_id", "id"}}



    for key in ("punch_in", "punch_out", "created_at"):

        if isinstance(out.get(key), datetime):

            out[key] = epoch_ms(dt_utc(out[key]))



    hist = []

    for entry in out.get("history", []) or []:

        e = dict(entry)

        if isinstance(e.get("at"), datetime):

            e["at"] = epoch_ms(dt_utc(e["at"]))



        changes = {}

        for k, v in (e.get("changes") or {}).items():

            changes[k] = epoch_ms(dt_utc(v)) if isinstance(v, datetime) else v

        e["changes"] = changes

        hist.append(e)



    if "history" in out:

        out["history"] = hist



    out.setdefault("half_day", False)

    out.setdefault("history", [])

    return out





def create_indexes() -> None:

    employees.create_index(

        [("emp_code", ASCENDING)],

        unique=True,

        name="uq_employee_code",

    )

    employees.create_index(

        [("department", ASCENDING), ("emp_code", ASCENDING)],

        name="ix_employee_department_code",

    )

    employees.create_index(

        [("department", ASCENDING), ("joined_on", ASCENDING)],

        name="ix_employee_department_joined",

    )

    logs.create_index(

        [("emp_code", ASCENDING), ("date", ASCENDING)],

        unique=True,

        name="uq_attendance_employee_date",

    )

    logs.create_index(

        [("date", DESCENDING), ("emp_code", ASCENDING)],

        name="ix_attendance_date_code",

    )

    logs.create_index(

        [("emp_code", ASCENDING), ("date", ASCENDING), ("status", ASCENDING)],

        name="ix_attendance_employee_date_status",

    )

    logs.create_index(

        [("date", ASCENDING), ("status", ASCENDING), ("late_minutes", DESCENDING)],

        name="ix_attendance_date_status_late",

    )





@app.on_event("startup")

def startup() -> None:

    # Startup intentionally fails if configured MongoDB is unavailable.

    create_indexes()





class EmployeeIn(BaseModel):

    model_config = ConfigDict(extra="forbid")



    emp_code: str

    name: str = Field(min_length=1, max_length=100)

    email: str = Field(max_length=120)

    department: str = Field(min_length=1, max_length=50)

    shift_start: str = "09:30"

    shift_end: str = "18:30"

    joined_on: str



    @field_validator("emp_code")

    @classmethod

    def valid_code(cls, v):

        if not EMP_RE.fullmatch(v or ""):

            raise ValueError("emp_code must match EMP followed by 4 to 6 digits")

        return v



    @field_validator("email")

    @classmethod

    def valid_email(cls, v):

        if not EMAIL_RE.fullmatch(v or ""):

            raise ValueError("Invalid email")

        return v



    @field_validator("shift_start", "shift_end")

    @classmethod

    def valid_shift(cls, v):

        if not TIME_RE.fullmatch(v or ""):

            raise ValueError("Shift time must be HH:MM")

        return v



    @field_validator("joined_on")

    @classmethod

    def valid_joined(cls, v):

        date.fromisoformat(v)

        return v



    @model_validator(mode="after")

    def shifts_differ(self):

        if self.shift_start == self.shift_end:

            raise ValueError("shift_start must differ from shift_end")

        return self





class PunchInIn(BaseModel):

    model_config = ConfigDict(extra="forbid")



    emp_code: str

    punched_at: Optional[int] = None

    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"



    @field_validator("punched_at")

    @classmethod

    def valid_timestamp(cls, v):

        if v is not None and not MIN_TS <= v <= MAX_TS:

            raise ValueError("punched_at must be epoch milliseconds")

        return v





class PunchOutIn(BaseModel):

    model_config = ConfigDict(extra="forbid")



    emp_code: str

    punched_at: Optional[int] = None



    @field_validator("punched_at")

    @classmethod

    def valid_timestamp(cls, v):

        if v is not None and not MIN_TS <= v <= MAX_TS:

            raise ValueError("punched_at must be epoch milliseconds")

        return v





class RegularizeIn(BaseModel):

    model_config = ConfigDict(extra="forbid")



    status: Optional[Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]] = None

    punch_in: Optional[int] = None

    punch_out: Optional[int] = None

    reason: str = Field(min_length=5, max_length=200)

    regularized_by: str = Field(min_length=1, max_length=50)



    @field_validator("punch_in", "punch_out")

    @classmethod

    def valid_timestamp(cls, v):

        if v is not None and not MIN_TS <= v <= MAX_TS:

            raise ValueError("Punch timestamps must be epoch milliseconds")

        return v





def page_params(page: int, page_size: int) -> None:

    if page < 1 or not 1 <= page_size <= 100:

        raise HTTPException(422, "page must be >= 1 and page_size must be 1..100")





def month_filter(first: date, last: date) -> dict:

    return {"date": {"$gte": first.isoformat(), "$lte": last.isoformat()}}





def weekdays_inclusive(start: date, end: date) -> int:

    if end < start:

        return 0

    days = (end - start).days + 1

    weeks, extra = divmod(days, 7)

    count = weeks * 5

    for i in range(extra):

        if (start + timedelta(days=weeks * 7 + i)).weekday() < 5:

            count += 1

    return count





def round_mongo_half_up(expr: Any, places: int = 2) -> dict:

    # MongoDB $round uses ties-to-even; use Decimal for final scalar calculations.

    return {"$round": [expr, places]}



@app.get("/health")

def health():

    try:

        client.admin.command("ping")

        return {"status": "ok"}

    except PyMongoError:

        raise HTTPException(503, "MongoDB unavailable")





@app.post("/employees", status_code=201)

def create_employee(body: EmployeeIn):

    doc = body.model_dump()

    doc["created_at"] = now_utc()

    try:

        employees.insert_one(doc)

    except DuplicateKeyError:

        raise HTTPException(409, "emp_code already exists")

    return output_doc(doc)





@app.get("/employees")

def list_employees(

    department: Optional[str] = None,

    page: int = Query(1, ge=1),

    page_size: int = Query(20, ge=1, le=100),

):

    page_params(page, page_size)

    q = {}
    if department is not None:
        q["department"] = {
        "$regex": f"^{re.escape(department)}$",
        "$options": "i"
    }
    total = employees.count_documents(q)

    items = list(

        employees.find(q, {"_id": 0})

        .sort("emp_code", ASCENDING)

        .skip((page - 1) * page_size)

        .limit(page_size)

    )

    return {

        "items": items,

        "total": total,

        "page": page,

        "page_size": page_size,

    }





@app.post("/attendance/punch-in", status_code=201)

def punch_in(body: PunchInIn):

    emp = employees.find_one({"emp_code": body.emp_code})

    if not emp:

        raise HTTPException(404, "Unknown employee")



    ts = from_epoch_ms(body.punched_at) if body.punched_at is not None else now_utc()

    day = attendance_date(ts, emp["shift_start"], emp["shift_end"]).isoformat()



    doc = {

        "emp_code": body.emp_code,

        "date": day,

        "status": body.status,

        "punch_in": ts,

        "punch_out": None,

        "work_hours": None,

        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),

        "overtime_minutes": 0,

        "half_day": False,

        "history": [],

    }

    try:

        logs.insert_one(doc)

    except DuplicateKeyError:

        raise HTTPException(409, "Already punched in for this attendance date")

    return output_doc(doc)





@app.post("/attendance/punch-out")

def punch_out(body: PunchOutIn):

    emp = employees.find_one({"emp_code": body.emp_code})

    if not emp:

        raise HTTPException(404, "Unknown employee")



    ts = from_epoch_ms(body.punched_at) if body.punched_at is not None else now_utc()

    record = logs.find_one(

        {"emp_code": body.emp_code, "punch_in": {"$lte": ts}},

        sort=[("punch_in", DESCENDING)],

    )

    if not record:

        raise HTTPException(404, "No punch-in found")

    if record.get("punch_out") is not None:

        raise HTTPException(409, "Already punched out")



    pin = dt_utc(record["punch_in"])

    if ts <= pin or ts > pin + timedelta(hours=24):

        raise HTTPException(422, "Punch-out must be after punch-in and within 24 hours")



    derived = derive_fields({**record, "punch_out": ts}, emp)

    updated = logs.find_one_and_update(

        {"_id": record["_id"], "punch_out": None},

        {

            "$set": {

                "punch_out": ts,

                "work_hours": derived["work_hours"],

                "overtime_minutes": derived["overtime_minutes"],

                "half_day": derived["half_day"],

            }

        },

        return_document=ReturnDocument.AFTER,

    )

    if not updated:

        raise HTTPException(409, "Already punched out")

    return output_doc(updated)





@app.get("/attendance")

def list_attendance(

    emp_code: Optional[str] = None,

    date_from: Optional[str] = None,

    date_to: Optional[str] = None,

    status: Optional[str] = None,

    page: int = Query(1, ge=1),

    page_size: int = Query(20, ge=1, le=100),

):

    page_params(page, page_size)



    if status is not None and status not in ALL_STATUS:

        raise HTTPException(422, "Invalid status")

    if date_from:

        parse_date(date_from)

    if date_to:

        parse_date(date_to)

    if date_from and date_to and date_from > date_to:

        raise HTTPException(422, "date_from must not be after date_to")



    q = {}

    if emp_code:

        q["emp_code"] = emp_code

    if date_from or date_to:

        q["date"] = {}

        if date_from:

            q["date"]["$gte"] = date_from

        if date_to:

            q["date"]["$lte"] = date_to

    if status:

        q["status"] = status



    total = logs.count_documents(q)

    docs = list(

        logs.find(q, {"_id": 0})

        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])

        .skip((page - 1) * page_size)

        .limit(page_size)

    )

    return {

        "items": [output_doc(d) for d in docs],

        "total": total,

        "page": page,

        "page_size": page_size,

    }





@app.patch("/attendance/{emp_code}/{day}")

def regularize_attendance(emp_code: str, day: str, body: RegularizeIn):

    parse_date(day)

    emp = employees.find_one({"emp_code": emp_code})

    if not emp:

        raise HTTPException(404, "Unknown employee")



    record = logs.find_one({"emp_code": emp_code, "date": day})

    if not record:

        raise HTTPException(404, "Attendance record not found")



    if body.status in {"ABSENT", "LEAVE"} and (

        "punch_in" in body.model_fields_set or "punch_out" in body.model_fields_set

    ):

        raise HTTPException(422, "ABSENT and LEAVE cannot be supplied with punch times")



    old = dict(record)

    old.setdefault("history", [])

    old.setdefault("half_day", False)

    candidate = dict(old)



    if body.status is not None:

        candidate["status"] = body.status

    if body.status in {"ABSENT", "LEAVE"}:

        candidate["punch_in"] = candidate["punch_out"] = None

    if "punch_in" in body.model_fields_set:

        candidate["punch_in"] = (

            from_epoch_ms(body.punch_in) if body.punch_in is not None else None

        )

    if "punch_out" in body.model_fields_set:

        candidate["punch_out"] = (

            from_epoch_ms(body.punch_out) if body.punch_out is not None else None

        )



    if candidate.get("status") in PRESENCE and candidate.get("punch_in") is None:

        raise HTTPException(422, "Presence status requires punch_in")



    if candidate.get("punch_in") is not None:

        actual_day = attendance_date(

            candidate["punch_in"], emp["shift_start"], emp["shift_end"]

        ).isoformat()

        if actual_day != day:

            raise HTTPException(422, "punch_in must belong to the attendance date")



    pin, pout = candidate.get("punch_in"), candidate.get("punch_out")

    if pout is not None and (

        pin is None or pout <= pin or pout > pin + timedelta(hours=24)

    ):

        raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")



    derived = derive_fields(candidate, emp)

    candidate.update(derived)



    changed = {}

    keys = (

        "status", "punch_in", "punch_out", "late_minutes",

        "work_hours", "overtime_minutes", "half_day",

    )

    for key in keys:

        before = old.get(key, False if key == "half_day" else None)

        after = candidate.get(key)

        if isinstance(before, datetime):

            before = dt_utc(before)

        if isinstance(after, datetime):

            after = dt_utc(after)

        if before != after:

            changed[key] = after



    if not changed:

        raise HTTPException(422, "Request does not change the record")



    history_entry = {

        "at": now_utc(),

        "by": body.regularized_by,

        "reason": body.reason,

        "changes": changed,

    }



    selector = {"_id": record["_id"]}

    for key in keys:

        selector[key] = record.get(key, False if key == "half_day" else None)



    updated = logs.find_one_and_update(

        selector,

        {

            "$set": {k: candidate.get(k) for k in keys},

            "$push": {"history": history_entry},

        },

        return_document=ReturnDocument.AFTER,

    )

    if not updated:

        raise HTTPException(409, "Record changed concurrently; retry")

    return output_doc(updated)





@app.get("/analytics/employees/{emp_code}/monthly")

def employee_monthly(emp_code: str, month: str):

    first, last = parse_month(month)

    emp = employees.find_one({"emp_code": emp_code})

    if not emp:

        raise HTTPException(404, "Unknown employee")



    joined = parse_date(emp["joined_on"])

    working_start = max(first, joined)

    working_days = (

        weekdays_inclusive(working_start, last) if working_start <= last else 0

    )



    pipeline = [

        {"$match": {"emp_code": emp_code, **month_filter(first, last)}},

        {

            "$group": {

                "_id": None,

                "leave_days": {

                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}

                },

                "late_count": {

                    "$sum": {

                        "$cond": [

                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},

                            1,

                            0,

                        ]

                    }

                },

                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},

                "overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},

            }

        },

    ]

    agg = list(logs.aggregate(pipeline))

    vals = agg[0] if agg else {}



    daily = list(logs.aggregate([

        {"$match": {"emp_code": emp_code, **month_filter(first, last)}},

        {"$addFields": {

            "_weekday": {

                "$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}

            }

        }},

        {"$group": {

            "_id": None,

            "present_weekdays": {"$sum": {"$cond": [

                {"$and": [

                    {"$in": ["$_weekday", [2, 3, 4, 5, 6]]},

                    {"$in": ["$status", list(PRESENCE)]},

                ]},

                {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]},

                0,

            ]}},

        }},

    ]))

    present_days = daily[0]["present_weekdays"] if daily else 0.0



    return {

        "emp_code": emp_code,

        "name": emp["name"],

        "department": emp["department"],

        "month": month,

        "working_days": working_days,

        "present_days": round_half_up(present_days, 2),

        "leave_days": vals.get("leave_days", 0),

        "late_count": vals.get("late_count", 0),

        "total_late_minutes": vals.get("total_late_minutes", 0),

        "overtime_minutes": vals.get("overtime_minutes", 0),

        "attendance_pct": (

            round_half_up(present_days / working_days * 100, 2)

            if working_days else None

        ),

    }





@app.get("/analytics/departments/summary")

def department_summary(month: str, department: Optional[str] = None):

    first, last = parse_month(month)

    emp_match: dict = {"joined_on": {"$lte": last.isoformat()}}

    if department is not None:

        emp_match["department"] = department



    pipeline = [

        {"$match": emp_match},

        {"$lookup": {

            "from": "attendance_logs",

            "let": {"code": "$emp_code"},

            "pipeline": [

                {"$match": {"$expr": {"$and": [

                    {"$eq": ["$emp_code", "$$code"]},

                    {"$gte": ["$date", first.isoformat()]},

                    {"$lte": ["$date", last.isoformat()]},

                ]}}},

                {"$project": {

                    "status": 1, "date": 1, "half_day": 1,

                    "work_hours": 1, "late_minutes": 1, "overtime_minutes": 1,

                }},

            ],

            "as": "att",

        }},

        {"$unwind": {"path": "$att", "preserveNullAndEmptyArrays": True}},

        {"$addFields": {

            "_weekday": {"$cond": [

                {"$ne": [{"$ifNull": ["$att.date", None]}, None]},

                {"$dayOfWeek": {"$dateFromString": {"dateString": "$att.date"}}},

                0,

            ]}

        }},

        {"$group": {

            "_id": "$department",

            "headcount": {"$addToSet": "$emp_code"},

            "present_days": {"$sum": {"$cond": [

                {"$and": [

                    {"$in": ["$_weekday", [2, 3, 4, 5, 6]]},

                    {"$in": ["$att.status", list(PRESENCE)]},

                ]},

                {"$cond": [{"$eq": [{"$ifNull": ["$att.half_day", False]}, True]}, 0.5, 1]},

                0,

            ]}},

            "hours": {"$push": {"$cond": [

                {"$and": [

                    {"$in": ["$att.status", list(PRESENCE)]},

                    {"$ne": [{"$ifNull": ["$att.work_hours", None]}, None]},

                ]},

                "$att.work_hours",

                None,

            ]}},

            "late_count": {"$sum": {"$cond": [

                {"$gt": [{"$ifNull": ["$att.late_minutes", 0]}, 0]}, 1, 0,

            ]}},

            "total_late_minutes": {"$sum": {"$ifNull": ["$att.late_minutes", 0]}},

            "leave_count": {"$sum": {"$cond": [{"$eq": ["$att.status", "LEAVE"]}, 1, 0]}},

            "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$att.status", "ON_DUTY"]}, 1, 0]}},

        }},

        {"$project": {

            "department": "$_id",

            "_id": 0,

            "headcount": {"$size": "$headcount"},

            "present_days": 1,

            "avg_work_hours": {"$avg": "$hours"},

            "late_count": 1,

            "total_late_minutes": 1,

            "leave_count": 1,

            "on_duty_count": 1,

        }},

        {"$sort": {"department": 1}},

    ]



    rows = list(employees.aggregate(pipeline))

    for row in rows:

        if row.get("avg_work_hours") is not None:

            row["avg_work_hours"] = round_half_up(row["avg_work_hours"], 2)

        row["present_days"] = round_half_up(row.get("present_days", 0), 2)

    return {"month": month, "items": rows}





@app.get("/analytics/leaderboard/late")

def late_leaderboard(

    month: str,

    limit: int = Query(10, ge=1, le=50),

    department: Optional[str] = None,

):

    first, last = parse_month(month)

    pipeline = [

        {"$match": {

            **month_filter(first, last),

            "late_minutes": {"$gt": 0},

        }},

        {"$group": {

            "_id": "$emp_code",

            "total_late_minutes": {"$sum": "$late_minutes"},

        }},

        {"$match": {"total_late_minutes": {"$gt": 0}}},

        {"$lookup": {

            "from": "employees",

            "localField": "_id",

            "foreignField": "emp_code",

            "as": "employee",

        }},

        {"$unwind": "$employee"},

    ]



    if department is not None:

        pipeline.append({"$match": {"employee.department": department}})



    pipeline += [

        {"$sort": {"total_late_minutes": -1, "_id": 1}},

        {"$setWindowFields": {

            "sortBy": {"total_late_minutes": -1},

            "output": {"rank": {"$rank": {}}},

        }},

        {"$match": {"rank": {"$lte": limit}}},

        {"$project": {

            "_id": 0,

            "emp_code": "$_id",

            "name": "$employee.name",

            "department": "$employee.department",

            "total_late_minutes": 1,

            "rank": 1,

        }},

    ]

    return {"month": month, "items": list(logs.aggregate(pipeline))}



@app.get("/analytics/departments/{department}/trend")

def department_trend(

    department: str,

    from_date: str = Query(..., alias="from"),

    to_date: str = Query(..., alias="to"),

):

    start, end = parse_date(from_date), parse_date(to_date)



    if end < start or (end - start).days + 1 > 92:

        raise HTTPException(422, "Date range must be ordered and no longer than 92 days")



    if not employees.find_one({"department": department}, {"_id": 1}):

        raise HTTPException(404, "Unknown department")



    # Build a calendar with a row for each date, including dates with no attendance.

    calendar = [

        datetime.combine(start + timedelta(days=i), time.min)

        for i in range((end - start).days + 1)

    ]



    calendar_pipeline = [

        {"$documents": [{"day": d} for d in calendar]},

        {"$set": {

            "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$day"}},

            "is_working_day": {"$and": [

                {"$gte": [{"$dayOfWeek": "$day"}, 2]},

                {"$lte": [{"$dayOfWeek": "$day"}, 6]},

            ]},

        }},

        {"$lookup": {

            "from": "employees",

            "let": {"d": "$date"},

            "pipeline": [

                {"$match": {"department": department}},

                {"$match": {"$expr": {"$lte": ["$joined_on", "$$d"]}}},

                {"$count": "n"},

            ],

            "as": "hc",

        }},

        {"$set": {

            "headcount": {"$ifNull": [{"$arrayElemAt": ["$hc.n", 0]}, 0]},

        }},

        {"$lookup": {

            "from": "attendance_logs",

            "let": {"d": "$date"},

            "pipeline": [

                {"$match": {"$expr": {"$eq": ["$date", "$$d"]}}},

                {"$lookup": {

                    "from": "employees",

                    "localField": "emp_code",

                    "foreignField": "emp_code",

                    "as": "employee",

                }},

                {"$unwind": "$employee"},

                {"$match": {"employee.department": department}},

            ],

            "as": "att",

        }},

        {"$set": {

            "present_count": {"$sum": {"$map": {

                "input": "$att",

                "as": "a",

                "in": {"$cond": [

                    {"$in": ["$$a.status", list(PRESENCE)]},

                    {"$cond": [

                        {"$eq": [{"$ifNull": ["$$a.half_day", False]}, True]},

                        0.5,

                        1,

                    ]},

                    0,

                ]},

            }}},

            "late_count": {"$size": {"$filter": {

                "input": "$att",

                "as": "a",

                "cond": {"$gt": [{"$ifNull": ["$$a.late_minutes", 0]}, 0]},

            }}},

        }},

        {"$set": {

            "attendance_rate": {"$cond": [

                {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},

                {"$round": [{"$divide": ["$present_count", "$headcount"]}, 4]},

                None,

            ]},

        }},

        {"$setWindowFields": {

            "sortBy": {"day": 1},

            "output": {

                "moving_avg_7d": {

                    "$avg": "$attendance_rate",

                    "window": {"documents": [-6, 0]},

                },

            },

        }},

        {"$project": {

            "_id": 0,

            "date": 1,

            "is_working_day": 1,

            "headcount": 1,

            "present_count": 1,

            "late_count": 1,

            "attendance_rate": 1,

            "moving_avg_7d": 1,

        }},

        {"$sort": {"date": 1}},

    ]



    rows = list(db.aggregate(calendar_pipeline, allowDiskUse=True))



    for row in rows:

        if row.get("attendance_rate") is not None:

            row["attendance_rate"] = round_half_up(row["attendance_rate"], 4)

        if row.get("moving_avg_7d") is not None:

            row["moving_avg_7d"] = round_half_up(row["moving_avg_7d"], 4)

        row["present_count"] = round_half_up(row.get("present_count", 0), 2)



    return {

        "department": department,

        "from": from_date,

        "to": to_date,

        "items": rows,

    }





@app.get("/admin/explain/{endpoint}")

def explain_endpoint(

    endpoint: Literal[

        "attendance_list",

        "employee_monthly",

        "department_summary",

        "late_leaderboard",

        "department_trend",

    ],

    emp_code: Optional[str] = None,

    month: Optional[str] = None,

    department: Optional[str] = None,

    limit: int = Query(10, ge=1, le=50),

    date_from: Optional[str] = None,

    date_to: Optional[str] = None,

    status: Optional[str] = None,

    from_date: Optional[str] = Query(None, alias="from"),

    to_date: Optional[str] = Query(None, alias="to"),

    page: int = Query(1, ge=1),

    page_size: int = Query(20, ge=1, le=100),

):

    page_params(page, page_size)



    if endpoint == "attendance_list":

        q = {}



        if emp_code:

            q["emp_code"] = emp_code

        if date_from or date_to:

            q["date"] = {}

            if date_from:

                parse_date(date_from)

                q["date"]["$gte"] = date_from

            if date_to:

                parse_date(date_to)

                q["date"]["$lte"] = date_to

        if status:

            if status not in ALL_STATUS:

                raise HTTPException(422, "Invalid status")

            q["status"] = status



        exp = logs.database.command(

            "explain",

            {

                "find": "attendance_logs",

                "filter": q,

                "sort": {"date": -1, "emp_code": 1},

                "skip": (page - 1) * page_size,

                "limit": page_size,

            },

            verbosity="executionStats",

        )



        return {

        "endpoint": endpoint,

        "collection": "attendance_logs",

        "explain": json.loads(json_util.dumps(exp))

    }



    if not month and endpoint != "department_trend":

        raise HTTPException(422, "month is required for this endpoint")



    if endpoint == "employee_monthly":

        if not emp_code:

            raise HTTPException(422, "emp_code is required")



        first, last = parse_month(month)

        pipeline = [

            {"$match": {"emp_code": emp_code, **month_filter(first, last)}},

            {"$group": {"_id": "$emp_code", "records": {"$sum": 1}}},

        ]

        exp = logs.database.command(

            "explain",

            {

                "aggregate": "attendance_logs",

                "pipeline": pipeline,

                "cursor": {},

            },

            verbosity="executionStats",

        )

        return {

            "endpoint": endpoint,

            "collection": "attendance_logs",

            "explain": json_util.loads(json_util.dumps(exp)),

        }



    if endpoint == "department_summary":

        first, last = parse_month(month)

        pipeline = [

            {"$match": month_filter(first, last)},

            {"$group": {"_id": "$date", "n": {"$sum": 1}}},

        ]

        exp = logs.database.command(

            "explain",

            {

                "aggregate": "attendance_logs",

                "pipeline": pipeline,

                "cursor": {},

            },

            verbosity="executionStats",

        )

        return {

            "endpoint": endpoint,

            "collection": "attendance_logs",

            "explain": json_util.loads(json_util.dumps(exp)),

        }



    if endpoint == "late_leaderboard":

        first, last = parse_month(month)

        pipeline = [

            {"$match": {

                **month_filter(first, last),

                "late_minutes": {"$gt": 0},

            }},

            {"$group": {

                "_id": "$emp_code",

                "total": {"$sum": "$late_minutes"},

            }},

        ]

        exp = logs.database.command(

            "explain",

            {

                "aggregate": "attendance_logs",

                "pipeline": pipeline,

                "cursor": {},

            },

            verbosity="executionStats",

        )

        return {

            "endpoint": endpoint,

            "collection": "attendance_logs",

            "explain": json_util.loads(json_util.dumps(exp)),

        }



    if endpoint == "department_trend":

        if not department or not from_date or not to_date:

            raise HTTPException(422, "department, from and to are required")



        start, end = parse_date(from_date), parse_date(to_date)

        if end < start or (end - start).days + 1 > 92:

            raise HTTPException(422, "Date range must be ordered and no longer than 92 days")



        pipeline = [

            {"$match": {"department": department}},

            {"$project": {"emp_code": 1, "joined_on": 1}},

        ]

        exp = employees.database.command(

            "explain",

            {

                "aggregate": "employees",

                "pipeline": pipeline,

                "cursor": {},

            },

            verbosity="executionStats",

        )

        return {

            "endpoint": endpoint,

            "collection": "employees",

            "explain": json_util.loads(json_util.dumps(exp)),

        }


