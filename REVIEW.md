# \# Code Review — Employee Attendance \& Analytics API

# 

# \## Defects Found in the Starter Code

# 

# | # | Where | What is wrong | How to notice it | How it was fixed |

# |---|---|---|---|---|

# | 1 | `compute\_late\_minutes()` | The helper uses naive datetime arithmetic and does not explicitly handle timezone conversion. | Test punch-in timestamps near midnight or in a different timezone. | Use consistent IST timestamps and validated time calculations. |

# | 2 | `compute\_work\_hours()` | It does not validate that punch-out occurs after punch-in. | Submit a punch-out timestamp earlier than punch-in. | Validate timestamp order and reject invalid punch-out requests. |

# | 3 | `compute\_overtime()` | The shift-end datetime is constructed with timezone information differently from the punch-out datetime, which can produce inconsistent calculations. | Test overtime around the shift end using timestamps with different timezone awareness. | Use consistent timezone-aware datetime calculations. |

# | 4 | `create\_employee()` | Duplicate checking is application-only and can race when simultaneous requests use the same employee code. | Send concurrent requests with the same `emp\_code`. | Add a unique database index and handle duplicate-key errors. |

# | 5 | `list\_employees()` | Pagination skips `page \* page\_size` records, so page 1 incorrectly skips the first page. The total count ignores the department filter. | Request page 1 and filter by department; compare items and total. | Use `(page - 1) \* page\_size` and count the filtered query. Validate page inputs. |

# | 6 | `punch\_in()` | It does not return 404 when the employee does not exist; it can fail while accessing employee shift data. The duplicate check is also vulnerable to concurrent requests. | Punch in with an unknown employee or send simultaneous requests for the same employee and date. | Validate employee existence, add a unique `(emp\_code, date)` index, and handle duplicate-key errors. |

# | 7 | `punch\_in()` | The timestamp uses local machine time rather than a consistent timezone, and falsey timestamp values such as `0` silently become the current time. | Submit `punched\_at=0` or compare timestamps across timezone settings. | Validate timestamp inputs and use explicit timezone conversion. |

# | 8 | `list\_attendance()` | It loads all matching documents into memory and sorts/paginates in Python, which is inefficient for large datasets. It also returns MongoDB `\_id` values in the response and does not validate pagination or filter values. | Test with many records, invalid page values, or status filters. | Apply sorting, skipping, limiting, and counting in MongoDB; validate inputs and format response IDs consistently. |

# | 9 | API contract coverage | The starter does not implement punch-out, attendance correction, monthly analytics, department summaries, late leaderboard, department trends, or explain endpoints. | Compare registered routes with `openapi.yaml`. | Implement the missing endpoints according to the API contract. |

# 

# \## Items Reviewed and Not Considered Defects

# 

# \- `health()` returns a simple status response, which is appropriate for a basic health endpoint.

# \- `compute\_work\_hours()` rounds the calculated duration to two decimal places, consistent with the required numeric precision, provided timestamps are validated first.

# \- `list\_attendance()` applies inclusive `$gte` and `$lte` bounds for the supplied date filters; this matches inclusive date-range behavior.

# \- The starter's use of a MongoDB query for employee lookup is appropriate, but the lookup needs correct error handling and database-level uniqueness protection.



