# \# Technical Decisions

# 

# \## Database Indexes

# 

# I added indexes to support the API's main query patterns and reduce unnecessary database scans. The employee collection uses a unique index on `emp\_code` to prevent duplicate employee codes, plus compound indexes for department and joining-date queries. The attendance collection uses a unique compound index on `(emp\_code, date)` to prevent multiple attendance records for the same employee on the same date. Additional indexes support date-based attendance listing, employee monthly analytics, and late-attendance queries.

# 

# \## Duplicate Punch-In Handling

# 

# Application-level duplicate checks alone are not sufficient when concurrent requests arrive. The unique attendance index provides database-level protection. Duplicate-key errors are handled by the API and returned as a conflict response.

# 

# \## Leaderboard Ties

# 

# Employees with equal total late minutes receive the same rank. Employee code is used as a deterministic secondary sort order so results remain predictable. Because tied employees can share a rank at the limit boundary, the response may contain more employees than the requested limit.

# 

# \## Department Summary and Headcount

# 

# The department summary begins with eligible employees and looks up their attendance records. This preserves employees who have no attendance records during the selected month, so headcount is not limited to employees who punched in.

# 

# \## Future Scaling

# 

# If analytics volume grows significantly, I would evaluate pre-aggregated daily attendance summaries and caching for frequently requested reports. These changes would be considered only after measuring query performance and confirming that the extra maintenance complexity is justified.

