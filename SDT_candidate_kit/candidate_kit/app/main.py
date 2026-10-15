"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from typing import Optional
from datetime import date as date_type, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from decimal import Decimal, ROUND_HALF_UP



load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")



@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employee_code",
    )

    db.employees.create_index(
        [("joined_on", 1)],
        name="idx_employees_joined_on",
    )

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_employee_date",
    )

    db.attendance_logs.create_index(
    [("date", -1), ("emp_code", 1)],
    name="idx_attendance_date_desc_emp_asc",

    )
    db.employees.create_index(
        [("department", 1)],
        name="idx_employees_department",
    )



# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    elapsed_seconds = (punch_in - start).total_seconds()

    if elapsed_seconds <= 600:
        return 0

    return int(elapsed_seconds // 60)


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    hours = seconds / Decimal("3600")
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(
        hour=h, minute=m, second=0, microsecond=0, tzinfo=IST
    )
    elapsed_seconds = (punch_out - end).total_seconds()
    if elapsed_seconds < 1800:
        return 0
    return int(elapsed_seconds // 60)

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    
class AttendancePatchIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    regularized_by: str
    reason: str

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(status_code=409, detail="emp_code already exists")

    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="emp_code already exists")

    doc.pop("_id", None)
    doc["created_at"] = int(doc["created_at"].timestamp() * 1000)
    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if department:
        q["department"] = department

    total = db.employees.count_documents(q)
    skip = (page - 1) * page_size

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    for item in items:
        created_at = item.get("created_at")
        if created_at is not None:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            item["created_at"] = int(created_at.timestamp() * 1000)

    return {"items": items, "total": total, "page": page, "page_size": page_size}



@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.status not in {"PRESENT", "WFH", "ON_DUTY"}:
        raise HTTPException(status_code=422, detail="Invalid attendance status")

    if body.punched_at is not None:
        if (
            isinstance(body.punched_at, bool)
            or not isinstance(body.punched_at, int)
            or body.punched_at < 100000000000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds",
            )

        try:
            ts = datetime.fromtimestamp(
                body.punched_at // 1000,
                tz=timezone.utc,
            )
        except (OverflowError, OSError, ValueError):
            raise HTTPException(
                status_code=422,
                detail="Invalid punched_at timestamp",
            )
    else:
        ts = datetime.now(timezone.utc)

    ts = ts.replace(microsecond=0)
    local_ts = ts.astimezone(IST)

    shift_start_time = datetime.strptime(
        emp["shift_start"], "%H:%M"
    ).time()
    shift_end_time = datetime.strptime(
        emp["shift_end"], "%H:%M"
    ).time()

    attendance_date = local_ts.date()

    if (
        shift_end_time <= shift_start_time
        and local_ts.time() < shift_end_time
    ):
        attendance_date -= timedelta(days=1)

    date_str = attendance_date.isoformat()

    shift_start = datetime.combine(
        attendance_date,
        shift_start_time,
        tzinfo=IST,
    )

    elapsed_minutes = int(
        (local_ts - shift_start).total_seconds() // 60
    )
    late_minutes = compute_late_minutes(local_ts, emp["shift_start"])

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Attendance already exists for this date",
        )

    doc.pop("_id", None)
    doc["punch_in"] = int(ts.timestamp() * 1000)

    return doc

def round_half_up(value: float, places: int = 2) -> float:
    from decimal import Decimal, ROUND_HALF_UP

    quantum = Decimal("1").scaleb(-places)
    return float(
        Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    )


@app.post("/attendance/punch-out") # complete new function
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is not None:
        if (
            isinstance(body.punched_at, bool)
            or not isinstance(body.punched_at, int)
            or body.punched_at < 100000000000
        ):
            raise HTTPException(
                status_code=422,
                detail="punched_at must be epoch milliseconds",
            )

        try:
            punched_at = datetime.fromtimestamp(
                body.punched_at // 1000,
                tz=timezone.utc,
            )
        except (OverflowError, OSError, ValueError):
            raise HTTPException(
                status_code=422,
                detail="Invalid punched_at timestamp",
            )
    else:
        punched_at = datetime.now(timezone.utc)

    punched_at = punched_at.replace(microsecond=0)

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punched_at},
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="No eligible punch-in record found",
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Attendance already punched out",
        )

    punch_in_at = record["punch_in"]
    if punch_in_at.tzinfo is None:
        punch_in_at = punch_in_at.replace(tzinfo=timezone.utc)

    punch_in_at = punch_in_at.replace(microsecond=0)

    elapsed_seconds = (punched_at - punch_in_at).total_seconds()

    if elapsed_seconds <= 0 or elapsed_seconds > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in and within 24 hours",
        )

    work_hours = round_half_up(elapsed_seconds / 3600, 2)

    shift_start_time = datetime.strptime(
        emp["shift_start"], "%H:%M"
    ).time()
    shift_end_time = datetime.strptime(
        emp["shift_end"], "%H:%M"
    ).time()

    attendance_date = date_type.fromisoformat(record["date"])
    shift_end_date = attendance_date

    if shift_end_time <= shift_start_time:
        shift_end_date += timedelta(days=1)

    shift_end_at = datetime.combine(
        shift_end_date, shift_end_time, tzinfo=IST
    ).astimezone(timezone.utc)

    elapsed_overtime = int(
        (punched_at - shift_end_at).total_seconds() // 60
    )
    overtime_minutes = elapsed_overtime if elapsed_overtime >= 30 else 0

    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punched_at,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance was already punched out",
        )

    return {
        "emp_code": record["emp_code"],
        "date": record["date"],
        "status": record["status"],
        "punch_in": int(punch_in_at.timestamp() * 1000),
        "punch_out": int(punched_at.timestamp() * 1000),
        "work_hours": work_hours,
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
        "history": record.get("history", []),
    }

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1), #change
    page_size: int = Query(20, ge=1, le=100), #change
):
    for value in (date_from, date_to): #change
        if value is not None:
            try:
                date_type.fromisoformat(value)
            except ValueError:
                raise HTTPException(
                status_code=422,
                detail="Dates must use YYYY-MM-DD format",
            )
    if date_from and date_to and date_from > date_to: #change
        raise HTTPException(
            status_code=422,
            detail="date_from must be on or before date_to",
    )
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
    total = db.attendance_logs.count_documents(q) #change
    skip = (page - 1) * page_size #change
    items = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )
    for item in items:
        for field in ("punch_in", "punch_out"):
            timestamp = item.get(field)

            if timestamp is not None:
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)

                item[field] = int(timestamp.timestamp() * 1000)

    return {"items": items, "total": total, "page": page, "page_size": page_size}


def build_department_trend_pipeline(department, start_day, end_day):
    start_text = start_day.isoformat()
    end_text = end_day.isoformat()

    pipeline = [
        {"$match": {"department": department}},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "employee_code": "$emp_code",
                    "joined_on": "$joined_on"
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", start_text]},
                                    {"$lte": ["$date", end_text]},
                                    {"$gte": ["$date", "$$joined_on"]}
                                ]
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": "$date",
                            "present_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$status",
                                                ["PRESENT", "WFH", "ON_DUTY"]
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$half_day", True]},
                                                0.5,
                                                1
                                            ]
                                        },
                                        0
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$gt": [
                                                {"$ifNull": ["$late_minutes", 0]},
                                                0
                                            ]
                                        },
                                        1,
                                        0
                                    ]
                                }
                            }
                        }
                    }
                ],
                "as": "daily_logs"
            }
        },
        {"$unwind": {"path": "$daily_logs", "preserveNullAndEmptyArrays": True}},
        {
            "$project": {
                "_id": 0,
                "date": {"$ifNull": ["$daily_logs._id", start_text]},
                "present_count": {"$ifNull": ["$daily_logs.present_count", 0]},
                "late_count": {"$ifNull": ["$daily_logs.late_count", 0]}
            }
        },
        {
            "$group": {
                "_id": "$date",
                "present_count": {"$sum": "$present_count"},
                "late_count": {"$sum": "$late_count"}
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$_id",
                "present_count": 1,
                "late_count": 1
            }
        },
        {
            "$set": {
                "date_value": {
                    "$dateFromString": {"dateString": "$date"}
                }
            }
        },
        {
            "$densify": {
                "field": "date_value",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [
                        datetime.combine(start_day, datetime.min.time()),
                        datetime.combine(
                            end_day + timedelta(days=1),
                            datetime.min.time()
                        )
                    ]
                }
            }
        },
        {
            "$set": {
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date_value"
                    }
                },
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
                "is_working_day": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$date_value"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$date_value"}, 6]}
                    ]
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"trend_date": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "$expr": {"$lte": ["$joined_on", "$$trend_date"]}
                        }
                    },
                    {"$count": "count"}
                ],
                "as": "headcount_result"
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_result.count", 0]},
                        0
                    ]
                }
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]}
                            ]
                        },
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    {
                                                        "$divide": [
                                                            "$present_count",
                                                            "$headcount"
                                                        ]
                                                    },
                                                    10000
                                                ]
                                            },
                                            0.5
                                        ]
                                    }
                                },
                                10000
                            ]
                        },
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date_value": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]}
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": {
                    "$cond": [
                        {"$eq": ["$moving_avg_7d", None]},
                        None,
                        {
                            "$divide": [
                                {
                                    "$floor": {
                                        "$add": [
                                            {
                                                "$multiply": [
                                                    "$moving_avg_7d",
                                                    10000
                                                ]
                                            },
                                            0.5
                                        ]
                                    }
                                },
                                10000
                            ]
                        }
                    ]
                }
            }
        },
        {"$sort": {"date": 1}}
    ]

    return pipeline


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to")
):
    try:
        start_day = date_type.fromisoformat(from_date)
        end_day = date_type.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format"
        )

    if end_day < start_day:
        raise HTTPException(
            status_code=422,
            detail="'to' must be greater than or equal to 'from'"
        )

    day_count = (end_day - start_day).days + 1

    if day_count > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days"
        )

    department_employee_count = db.employees.count_documents(
        {"department": department}
    )

    if department_employee_count == 0:
        raise HTTPException(
            status_code=404,
            detail="Department not found"
        )

    start_text = start_day.isoformat()
    end_text = end_day.isoformat()

    pipeline = build_department_trend_pipeline(
        department,
        start_day,
        end_day
    )

    items = list(db.employees.aggregate(pipeline))

    return {
        "department": department,
        "items": items
    }




@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
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
    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(
            status_code=422,
            detail="Unsupported endpoint",
        )

    if endpoint == "attendance_list":
        query = {}

        if emp_code:
            query["emp_code"] = emp_code

        if date_from:
            query.setdefault("date", {})["$gte"] = date_from

        if date_to:
            query.setdefault("date", {})["$lte"] = date_to

        if status:
            query["status"] = status

        for value in (date_from, date_to):
            if value is not None:
                try:
                    parsed = date_type.fromisoformat(value)
                    if parsed.isoformat() != value:
                        raise ValueError
                except ValueError:
                    raise HTTPException(
                        status_code=422,
                        detail="Dates must use YYYY-MM-DD format",
                    )

        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail="date_from must be on or before date_to",
            )

        explain = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size,
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "collection": "attendance_logs",
            "explain": explain,
        }

    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(
                status_code=422,
                detail="emp_code and month are required",
            )

        try:
            month_start = datetime.strptime(month, "%Y-%m").date()
            if month_start.strftime("%Y-%m") != month:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid month",
            )

        employee = db.employees.find_one(
            {"emp_code": emp_code},
            {"_id": 0, "emp_code": 1, "joined_on": 1},
        )

        if employee is None:
            raise HTTPException(
                status_code=404,
                detail="Employee not found",
            )

        from calendar import monthrange

        last_day = monthrange(
            month_start.year,
            month_start.month,
        )[1]

        month_end = date_type(
            month_start.year,
            month_start.month,
            last_day,
        )

        joined_on = date_type.fromisoformat(employee["joined_on"])
        effective_start = max(month_start, joined_on)

        attendance_pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {
                        "$gte": effective_start.isoformat(),
                        "$lte": month_end.isoformat(),
                    },
                }
            },
            {
                "$set": {
                    "_day_of_week": {
                        "$dayOfWeek": {
                            "$dateFromString": {
                                "dateString": "$date"
                            }
                        }
                    }
                }
            },
            {
                "$group": {
                    "_id": None,
                    "present_days": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$and": [
                                        {
                                            "$in": [
                                                "$status",
                                                ["PRESENT", "WFH", "ON_DUTY"],
                                            ]
                                        },
                                        {"$gte": ["$_day_of_week", 2]},
                                        {"$lte": ["$_day_of_week", 6]},
                                    ]
                                },
                                {
                                    "$cond": [
                                        {"$eq": ["$half_day", True]},
                                        0.5,
                                        1,
                                    ]
                                },
                                0,
                            ]
                        }
                    },
                    "leave_days": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$status", "LEAVE"]},
                                1,
                                0,
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$gt": [
                                        {"$ifNull": ["$late_minutes", 0]},
                                        0,
                                    ]
                                },
                                1,
                                0,
                            ]
                        }
                    },
                    "total_late_minutes": {
                        "$sum": {"$ifNull": ["$late_minutes", 0]}
                    },
                    "total_overtime_minutes": {
                        "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                    },
                }
            },
        ]

        attendance_explain = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": attendance_pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        working_days_pipeline = [
            {
                "$match": {
                    "emp_code": emp_code
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "days": {
                        "$map": {
                            "input": {
                                "$range": [
                                    0,
                                    {
                                        "$dateDiff": {
                                            "startDate": {
                                                "$dateFromString": {
                                                    "dateString": effective_start.isoformat()
                                                }
                                            },
                                            "endDate": {
                                                "$dateAdd": {
                                                    "startDate": {
                                                        "$dateFromString": {
                                                            "dateString": month_end.isoformat()
                                                        }
                                                    },
                                                    "unit": "day",
                                                    "amount": 1,
                                                }
                                            },
                                            "unit": "day",
                                        }
                                    },
                                ]
                            },
                            "as": "offset",
                            "in": {
                                "$dayOfWeek": {
                                    "$dateAdd": {
                                        "startDate": {
                                            "$dateFromString": {
                                                "dateString": effective_start.isoformat()
                                            }
                                        },
                                        "unit": "day",
                                        "amount": "$$offset",
                                    }
                                }
                            },
                        }
                    },
                }
            },
            {
                "$project": {
                    "working_days": {
                        "$size": {
                            "$filter": {
                                "input": "$days",
                                "as": "day",
                                "cond": {
                                    "$and": [
                                        {"$gte": ["$$day", 2]},
                                        {"$lte": ["$$day", 6]},
                                    ]
                                },
                            }
                        }
                    }
                }
            },
        ]

        working_days_explain = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline": working_days_pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

        return {
            "endpoint": endpoint,
            "attendance_explain": attendance_explain,
            "working_days_explain": working_days_explain,
        }

    if endpoint == "department_summary":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required",
            )

        try:
            month_start = datetime.strptime(month, "%Y-%m").date()
            if month_start.strftime("%Y-%m") != month:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid month",
            )

        from calendar import monthrange

        last_day = monthrange(
            month_start.year,
            month_start.month,
        )[1]

        month_end = date_type(
            month_start.year,
            month_start.month,
            last_day,
        )

        pipeline = [
            {
                "$match": {
                    "joined_on": {
                        "$lte": month_end.isoformat()
                    }
                }
            },
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {
                        "employee_code": "$emp_code",
                        "join_date": "$joined_on",
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$emp_code", "$$employee_code"]},
                                        {"$gte": ["$date", month_start.isoformat()]},
                                        {"$lte": ["$date", month_end.isoformat()]},
                                        {"$gte": ["$date", "$$join_date"]},
                                    ]
                                }
                            }
                        },
                        {
                            "$set": {
                                "_day_of_week": {
                                    "$dayOfWeek": {
                                        "$dateFromString": {
                                            "dateString": "$date"
                                        }
                                    }
                                }
                            }
                        },
                        {
                            "$group": {
                                "_id": None,
                                "present_days": {
                                    "$sum": {
                                        "$cond": [
                                            {
                                                "$and": [
                                                    {
                                                        "$in": [
                                                            "$status",
                                                            ["PRESENT", "WFH", "ON_DUTY"],
                                                        ]
                                                    },
                                                    {"$gte": ["$_day_of_week", 2]},
                                                    {"$lte": ["$_day_of_week", 6]},
                                                ]
                                            },
                                            {
                                                "$cond": [
                                                    {"$eq": ["$half_day", True]},
                                                    0.5,
                                                    1,
                                                ]
                                            },
                                            0,
                                        ]
                                    }
                                },
                                "work_hours_sum": {
                                    "$sum": {
                                        "$cond": [
                                            {
                                                "$and": [
                                                    {
                                                        "$in": [
                                                            "$status",
                                                            ["PRESENT", "WFH", "ON_DUTY"],
                                                        ]
                                                    },
                                                    {"$ne": ["$work_hours", None]},
                                                ]
                                            },
                                            "$work_hours",
                                            0,
                                        ]
                                    }
                                },
                                "work_hours_count": {
                                    "$sum": {
                                        "$cond": [
                                            {
                                                "$and": [
                                                    {
                                                        "$in": [
                                                            "$status",
                                                            ["PRESENT", "WFH", "ON_DUTY"],
                                                        ]
                                                    },
                                                    {"$ne": ["$work_hours", None]},
                                                ]
                                            },
                                            1,
                                            0,
                                        ]
                                    }
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
                                "total_late_minutes": {
                                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                                },
                                "leave_count": {
                                    "$sum": {
                                        "$cond": [
                                            {"$eq": ["$status", "LEAVE"]},
                                            1,
                                            0,
                                        ]
                                    }
                                },
                                "on_duty_count": {
                                    "$sum": {
                                        "$cond": [
                                            {"$eq": ["$status", "ON_DUTY"]},
                                            1,
                                            0,
                                        ]
                                    }
                                },
                            }
                        },
                    ],
                    "as": "attendance_summary",
                }
            },
            {
                "$set": {
                    "summary": {
                        "$ifNull": [
                            {"$arrayElemAt": ["$attendance_summary", 0]},
                            {
                                "present_days": 0,
                                "work_hours_sum": 0,
                                "work_hours_count": 0,
                                "late_count": 0,
                                "total_late_minutes": 0,
                                "leave_count": 0,
                                "on_duty_count": 0,
                            },
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": "$department",
                    "headcount": {"$sum": 1},
                    "present_days": {"$sum": "$summary.present_days"},
                    "work_hours_sum": {"$sum": "$summary.work_hours_sum"},
                    "work_hours_count": {"$sum": "$summary.work_hours_count"},
                    "late_count": {"$sum": "$summary.late_count"},
                    "total_late_minutes": {"$sum": "$summary.total_late_minutes"},
                    "leave_count": {"$sum": "$summary.leave_count"},
                    "on_duty_count": {"$sum": "$summary.on_duty_count"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "department": "$_id",
                    "headcount": 1,
                    "present_days": 1,
                    "avg_work_hours": {
                        "$cond": [
                            {"$gt": ["$work_hours_count", 0]},
                            {
                                "$round": [
                                    {
                                        "$divide": [
                                            "$work_hours_sum",
                                            "$work_hours_count",
                                        ]
                                    },
                                    2,
                                ]
                            },
                            None,
                        ]
                    },
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "leave_count": 1,
                    "on_duty_count": 1,
                }
            },
            {"$sort": {"department": 1}},
        ]

        explain = db.command(
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
            "explain": explain,
        }

    if endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(
                status_code=422,
                detail="month is required",
            )

        try:
            month_start = datetime.strptime(month, "%Y-%m").date()
            if month_start.strftime("%Y-%m") != month:
                raise ValueError
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Invalid month",
            )

        from calendar import monthrange

        last_day = monthrange(
            month_start.year,
            month_start.month,
        )[1]

        month_end = date_type(
            month_start.year,
            month_start.month,
            last_day,
        )

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": month_start.isoformat(),
                        "$lte": month_end.isoformat(),
                    },
                    "late_minutes": {"$gt": 0},
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {"$gt": ["$late_minutes", 0]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
            {"$match": {"total_late_minutes": {"$gt": 0}}},
            {
                "$lookup": {
                    "from": "employees",
                    "localField": "_id",
                    "foreignField": "emp_code",
                    "as": "employee",
                }
            },
            {"$unwind": "$employee"},
        ]

        if department:
            pipeline.append(
                {"$match": {"employee.department": department}}
            )

        pipeline.extend(
            [
                {
                    "$setWindowFields": {
                        "sortBy": {"total_late_minutes": -1},
                        "output": {"rank": {"$rank": {}}},
                    }
                },
                {"$match": {"rank": {"$lte": limit}}},
                {
                    "$sort": {
                        "total_late_minutes": -1,
                        "employee.emp_code": 1,
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "rank": 1,
                        "emp_code": "$employee.emp_code",
                        "name": "$employee.name",
                        "department": "$employee.department",
                        "total_late_minutes": 1,
                        "late_count": 1,
                    }
                },
            ]
        )

        explain = db.command(
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
            "explain": explain,
        }


    if endpoint == "department_trend":
        if not department or not date_from or not date_to:
            raise HTTPException(
                status_code=422,
                detail="department, date_from, and date_to are required"
    )

        
        try:
            start_day = date_type.fromisoformat(date_from)
            end_day = date_type.fromisoformat(date_to)

            if start_day.isoformat() != date_from or end_day.isoformat() != date_to:
                raise ValueError

        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="Dates must use YYYY-MM-DD format"
            )


        if end_day < start_day:
            raise HTTPException(
                status_code=422,
                detail="'to' must be greater than or equal to 'from'"
            )

        if (end_day - start_day).days + 1 > 92:
            raise HTTPException(
                status_code=422,
                detail="Date range cannot exceed 92 days"
            )

        if db.employees.count_documents({"department": department}) == 0:
            raise HTTPException(
                status_code=404,
                detail="Department not found"
            )

        pipeline = build_department_trend_pipeline(
            department,
            start_day,
            end_day
        )

        explain = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {}
            },
            verbosity="executionStats"
        )

        return {
            "endpoint": endpoint,
            "collection": "employees",
            "explain": explain
        }


    raise HTTPException(
        status_code=501,
        detail=f"Explain is not implemented yet for {endpoint}",
    )






@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None
):
    from calendar import monthrange

    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    last_day = monthrange(month_start.year, month_start.month)[1]
    month_end = date_type(
        month_start.year,
        month_start.month,
        last_day
    )

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lte": month_end.isoformat()
                },
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0
                        ]
                    }
                }
            }
        },
        {
            "$match": {
                "total_late_minutes": {"$gt": 0}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
        {
            "$match": {
                **({"employee.department": department} if department else {})
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {
                    "total_late_minutes": -1
                },
                "output": {
                    "rank": {
                        "$rank": {}
                    }
                }
            }
        },
        {
            "$match": {
                "rank": {"$lte": limit}
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "employee.emp_code": 1
            }
        },
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$employee.emp_code",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1
            }
        }
    ]

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
):
    from calendar import monthrange

    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    last_day = monthrange(month_start.year, month_start.month)[1]
    month_end = date_type(
        month_start.year,
        month_start.month,
        last_day
    )

    pipeline = [
        {
            "$match": {
                "joined_on": {"$lte": month_end.isoformat()}
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "employee_code": "$emp_code",
                    "join_date": "$joined_on"
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", month_start.isoformat()]},
                                    {"$lte": ["$date", month_end.isoformat()]},
                                    {"$gte": ["$date", "$$join_date"]}
                                ]
                            }
                        }
                    },
                    {
                        "$set": {
                            "_day_of_week": {
                                "$dayOfWeek": {
                                    "$dateFromString": {
                                        "dateString": "$date"
                                    }
                                }
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_days": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {
                                                    "$in": [
                                                        "$status",
                                                        ["PRESENT", "WFH", "ON_DUTY"]
                                                    ]
                                                },
                                                {"$gte": ["$_day_of_week", 2]},
                                                {"$lte": ["$_day_of_week", 6]}
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$half_day", True]},
                                                0.5,
                                                1
                                            ]
                                        },
                                        0
                                    ]
                                }
                            },
                            "work_hours_sum": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {
                                                    "$in": [
                                                        "$status",
                                                        ["PRESENT", "WFH", "ON_DUTY"]
                                                    ]
                                                },
                                                {"$ne": ["$work_hours", None]}
                                            ]
                                        },
                                        "$work_hours",
                                        0
                                    ]
                                }
                            },
                            "work_hours_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {
                                                    "$in": [
                                                        "$status",
                                                        ["PRESENT", "WFH", "ON_DUTY"]
                                                    ]
                                                },
                                                {"$ne": ["$work_hours", None]}
                                            ]
                                        },
                                        1,
                                        0
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                                        1,
                                        0
                                    ]
                                }
                            },
                            "total_late_minutes": {
                                "$sum": {"$ifNull": ["$late_minutes", 0]}
                            },
                            "leave_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$status", "LEAVE"]},
                                        1,
                                        0
                                    ]
                                }
                            },
                            "on_duty_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$status", "ON_DUTY"]},
                                        1,
                                        0
                                    ]
                                }
                            }
                        }
                    }
                ],
                "as": "attendance_summary"
            }
        },
        {
            "$set": {
                "summary": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$attendance_summary", 0]},
                        {
                            "present_days": 0,
                            "work_hours_sum": 0,
                            "work_hours_count": 0,
                            "late_count": 0,
                            "total_late_minutes": 0,
                            "leave_count": 0,
                            "on_duty_count": 0
                        }
                    ]
                }
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {"$sum": "$summary.present_days"},
                "work_hours_sum": {"$sum": "$summary.work_hours_sum"},
                "work_hours_count": {"$sum": "$summary.work_hours_count"},
                "late_count": {"$sum": "$summary.late_count"},
                "total_late_minutes": {"$sum": "$summary.total_late_minutes"},
                "leave_count": {"$sum": "$summary.leave_count"},
                "on_duty_count": {"$sum": "$summary.on_duty_count"}
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {
                            "$round": [
                                {
                                    "$divide": [
                                        "$work_hours_sum",
                                        "$work_hours_count"
                                    ]
                                },
                                2
                            ]
                        },
                        None
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1
            }
        },
        {"$sort": {"department": 1}}
    ]

    results = list(db.employees.aggregate(pipeline))

    return {
    "month": month,
    "items": results
}

    
@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
):
    try:
        month_start = datetime.strptime(month, "%Y-%m").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "emp_code": 1, "joined_on": 1}
    )

    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    from calendar import monthrange

    last_day = monthrange(month_start.year, month_start.month)[1]
    month_end = date_type(
        month_start.year,
        month_start.month,
        last_day
    )

    joined_on = date_type.fromisoformat(employee["joined_on"])
    effective_start = max(month_start, joined_on)

    attendance_pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": effective_start.isoformat(),
                    "$lte": month_end.isoformat()
                }
            }
        },
        {
            "$set": {
                "_day_of_week": {
                    "$dayOfWeek": {
                        "$dateFromString": {
                            "dateString": "$date"
                        }
                    }
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    },
                                    {"$gte": ["$_day_of_week", 2]},
                                    {"$lte": ["$_day_of_week", 6]}
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$ifNull": ["$late_minutes", 0]},
                                    0
                                ]
                            },
                            1,
                            0
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                }
            }
        }
    ]

    attendance_result = list(
        db.attendance_logs.aggregate(attendance_pipeline)
    )

    totals = attendance_result[0] if attendance_result else {
        "present_days": 0,
        "leave_days": 0,
        "late_count": 0,
        "total_late_minutes": 0,
        "total_overtime_minutes": 0
    }

    working_days_pipeline = [
        {
            "$match": {
                "emp_code": emp_code
            }
        },
        {
            "$project": {
                "_id": 0,
                "days": {
                    "$map": {
                        "input": {
                            "$range": [
                                0,
                                {
                                    "$dateDiff": {
                                        "startDate": {
                                            "$dateFromString": {
                                                "dateString": effective_start.isoformat()
                                            }
                                        },
                                        "endDate": {
                                            "$dateAdd": {
                                                "startDate": {
                                                    "$dateFromString": {
                                                        "dateString": month_end.isoformat()
                                                    }
                                                },
                                                "unit": "day",
                                                "amount": 1
                                            }
                                        },
                                        "unit": "day"
                                    }
                                }
                            ]
                        },
                        "as": "offset",
                        "in": {
                            "$dayOfWeek": {
                                "$dateAdd": {
                                    "startDate": {
                                        "$dateFromString": {
                                            "dateString": effective_start.isoformat()
                                        }
                                    },
                                    "unit": "day",
                                    "amount": "$$offset"
                                }
                            }
                        }
                    }
                }
            }
        },
        {
            "$project": {
                "working_days": {
                    "$size": {
                        "$filter": {
                            "input": "$days",
                            "as": "day",
                            "cond": {
                                "$and": [
                                    {"$gte": ["$$day", 2]},
                                    {"$lte": ["$$day", 6]}
                                ]
                            }
                        }
                    }
                }
            }
        }
    ]

    working_days_result = list(
        db.employees.aggregate(working_days_pipeline)
    )

    working_days = (
        working_days_result[0]["working_days"]
        if working_days_result
        else 0
    )

    present_days = totals["present_days"]

    attendance_pct = (
        round_half_up(present_days / working_days * 100, 4)
        if working_days > 0
        else None
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": totals["leave_days"],
        "late_count": totals["late_count"],
        "total_late_minutes": totals["total_late_minutes"],
        "total_overtime_minutes": totals["total_overtime_minutes"],
        "attendance_pct": attendance_pct
    }

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: AttendancePatchIn,
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    try:
        attendance_day = date_type.fromisoformat(date)
        if attendance_day.isoformat() != date:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date")

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date}
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    supplied = body.model_fields_set
    new_status = body.status if "status" in supplied else record.get("status")
    valid_statuses = {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}

    if new_status not in valid_statuses:
        raise HTTPException(status_code=422, detail="Invalid status")

    if new_status in {"ABSENT", "LEAVE"} and (
        ("punch_in" in supplied and body.punch_in is not None)
        or ("punch_out" in supplied and body.punch_out is not None)
    ):
        raise HTTPException(
            status_code=422,
            detail="ABSENT and LEAVE cannot include punch times",
        )

    def parse_timestamp(value):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 100000000000
        ):
            raise HTTPException(
                status_code=422,
                detail="Timestamps must be epoch milliseconds",
            )
        try:
            return datetime.fromtimestamp(
                value // 1000, tz=timezone.utc
            ).replace(microsecond=0)
        except (OverflowError, OSError, ValueError):
            raise HTTPException(status_code=422, detail="Invalid timestamp")

    def ensure_utc(value):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def to_epoch_ms(value):
        value = ensure_utc(value)
        return int(value.timestamp() * 1000) if value is not None else None

    punch_in = ensure_utc(record.get("punch_in"))
    punch_out = ensure_utc(record.get("punch_out"))

    if "punch_in" in supplied:
        punch_in = (
            parse_timestamp(body.punch_in)
            if body.punch_in is not None
            else None
        )

    if "punch_out" in supplied:
        punch_out = (
            parse_timestamp(body.punch_out)
            if body.punch_out is not None
            else None
        )

    if new_status in {"ABSENT", "LEAVE"}:
        punch_in = None
        punch_out = None
    elif punch_in is None:
        raise HTTPException(
            status_code=422,
            detail="Presence status requires punch_in",
        )

    shift_start_time = datetime.strptime(
        emp["shift_start"], "%H:%M"
    ).time()
    shift_end_time = datetime.strptime(
        emp["shift_end"], "%H:%M"
    ).time()

    if punch_in is not None:
        local_punch_in = punch_in.astimezone(IST)
        expected_date = local_punch_in.date()

        if (
            shift_end_time <= shift_start_time
            and local_punch_in.time() < shift_end_time
        ):
            expected_date -= timedelta(days=1)

        if expected_date.isoformat() != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the attendance date",
            )

        shift_start = datetime.combine(
            expected_date, shift_start_time, tzinfo=IST
        )
        elapsed_minutes = int(
            (local_punch_in - shift_start).total_seconds() // 60
        )
        late_minutes = elapsed_minutes if elapsed_minutes > 10 else 0
    else:
        late_minutes = 0

    if punch_out is not None:
        if punch_in is None or punch_out <= punch_in:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in",
            )

        elapsed_seconds = (punch_out - punch_in).total_seconds()
        if elapsed_seconds > 24 * 60 * 60:
            raise HTTPException(
                status_code=422,
                detail="Punch duration cannot exceed 24 hours",
            )

        work_hours = round_half_up(elapsed_seconds / 3600, 2)

        shift_end_date = attendance_day
        if shift_end_time <= shift_start_time:
            shift_end_date += timedelta(days=1)

        shift_end = datetime.combine(
            shift_end_date, shift_end_time, tzinfo=IST
        ).astimezone(timezone.utc)

        # Both values are now timezone-aware UTC datetimes.
        punch_out_utc = ensure_utc(punch_out)
        shift_end_utc = ensure_utc(shift_end)

        overtime_elapsed = int(
            (punch_out_utc - shift_end_utc).total_seconds() // 60
        )
        overtime_minutes = (
            overtime_elapsed if overtime_elapsed >= 30 else 0
        )
        half_day = work_hours < 4.50
    else:
        work_hours = None
        overtime_minutes = 0
        half_day = False

    final_values = {
        "status": new_status,
        "punch_in": punch_in,
        "punch_out": punch_out,
        "late_minutes": late_minutes,
        "work_hours": work_hours,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    changes = {}
    for field, new_value in final_values.items():
        old_value = record.get(field)

        if field in {"punch_in", "punch_out"}:
            old_compare = to_epoch_ms(old_value)
            new_compare = to_epoch_ms(new_value)
            if old_compare != new_compare:
                changes[field] = {
                    "from": old_compare,
                    "to": new_compare,
                }
        elif old_value != new_value:
            changes[field] = {
                "from": old_value,
                "to": new_value,
            }

    if not changes:
        raise HTTPException(status_code=422, detail="Request changes nothing")

    history_entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    original_history = record.get("history", [])

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "history": original_history,
            "status": record.get("status"),
            "punch_in": record.get("punch_in"),
            "punch_out": record.get("punch_out"),
        },
        {
            "$set": final_values,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance changed concurrently; retry the request",
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0},
    )

    for field in ("punch_in", "punch_out"):
        updated[field] = to_epoch_ms(updated.get(field))

    for entry in updated.get("history", []):
        entry["at"] = to_epoch_ms(entry.get("at"))
        for change in entry.get("changes", {}).values():
            for side in ("from", "to"):
                if isinstance(change.get(side), datetime):
                    change[side] = to_epoch_ms(change[side])

    return updated


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
