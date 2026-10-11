# Employee Attendance & Analytics API

A REST API built with FastAPI and MongoDB for managing employees, recording attendance, and generating attendance analytics.

## Features
- Employee creation and paginated employee listing
- Attendance punch-in and punch-out
- Attendance record listing and correction
- Monthly employee attendance analytics
- Department attendance summaries
- Late-arrival leaderboard
- Department attendance trends
- Query execution-plan inspection

## Tech Stack
- Python
- FastAPI
- MongoDB
- PyMongo
- Pydantic

## Setup
1. Create and activate a Python virtual environment.
2. Install dependencies: `pip install -r requirements.txt`
3. Configure the required MongoDB environment variables privately.
4. Start the API: `uvicorn app.main:app --reload`
5. Open `http://127.0.0.1:8000/docs` in your browser.

## Configuration
Use your own MongoDB connection string and required environment variables. Never commit `.env` or publish credentials.

## Testing
Use `/docs` to explore the API endpoints and their request parameters and responses.

## Database Performance
The application defines indexes for employee lookups, attendance uniqueness, date-based listing, and analytics queries. The explain endpoint exposes MongoDB query execution statistics.

## Notes
- Attendance calculations follow the application business rules.
- Keep database credentials and local environment files out of version control.
