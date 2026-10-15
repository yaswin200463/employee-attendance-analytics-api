# Employee Attendance & Analytics API

## Project Overview

The Employee Attendance & Analytics API is a backend application built using FastAPI and MongoDB. It provides endpoints for employee management, attendance tracking, punch-in and punch-out operations, attendance regularization, and analytics.

## Technologies Used

- Python
- FastAPI
- Uvicorn
- MongoDB
- PyMongo
- Pydantic
- Python-dotenv

## Features

- Employee creation and listing
- Employee filtering, sorting, and pagination
- Attendance listing with filters and pagination
- Employee punch-in and punch-out operations
- Employee and attendance data validation
- Duplicate attendance request handling
- Attendance date-range validation
- Attendance regularization and history
- Work-hour, late-minute, and overtime calculations
- Monthly attendance analytics
- Department-wise attendance summaries
- Late-arrival leaderboard
- Department attendance trends
- Database query-plan endpoints
- Health-check endpoint

## Project Structure


## Project Structure

```text
candidate_kit/
├── app/
│   ├── __init__.py
│   └── main.py
├── sample_data/
│   ├── attendance_logs.json
│   └── employees.json
├── DATA_MODEL.md
├── DECISIONS.md
├── openapi.yaml
├── PROBLEM_STATEMENT.docx
├── README.md
├── requirements.txt
├── REVIEW.md
└── sample_seed.py
```

## Prerequisites

- Python
- MongoDB 6.0 or newer
- Required Python dependencies

## Installation and Setup

### 1. Create a virtual environment

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

On Linux or macOS:

```bash
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file based on `.env.example` and configure the MongoDB connection.

Example:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=hrone
```

### 4. Start MongoDB

Ensure that your local MongoDB server or MongoDB Atlas instance is running.

To run MongoDB using Docker:

```bash
docker run -p 27017:27017 mongo:7
```

### 5. Load sample data

```bash
python sample_seed.py
```

### 6. Run the application

```bash
uvicorn app.main:app --port 8000
```

To enable automatic reload during development:

```bash
uvicorn app.main:app --port 8000 --reload
```

## API Documentation

After starting the application, open:

- **Swagger UI:** http://127.0.0.1:8000/docs
- **OpenAPI JSON:** http://127.0.0.1:8000/openapi.json

## Testing

The following areas were covered during local testing:

- Employee creation, listing, filtering, and pagination
- Attendance filtering, date ranges, and pagination
- Punch-in and punch-out validation
- Duplicate attendance request handling
- Date and timestamp validation
- Attendance regularization and history
- Monthly and department analytics
- Late-arrival leaderboard and department trends
- Health-check endpoint
- Database query plans and index usage
- Concurrent punch-in and punch-out requests

## Code Review and Design Decisions

- `REVIEW.md` contains code review findings, fixes, and testing results.
- `DECISIONS.md` documents the technical decisions made during development.

## Security Notes

- Store database credentials in environment variables.
- Do not commit `.env` or other secret files to version control.
- Keep application code in `app/main.py`.
- Ensure MongoDB is available before starting database-dependent operations.