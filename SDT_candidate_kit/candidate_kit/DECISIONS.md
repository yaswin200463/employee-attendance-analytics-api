# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?

I created five indexes. uq_employee_code prevents duplicate employee codes. idx_employees_joined_on supports joining-date queries. uq_attendance_employee_date prevents duplicate daily attendance for an employee. idx_attendance_date_desc_emp_asc supports attendance sorting by date descending and employee code ascending. idx_employees_department supports department filtering. I considered adding a separate status index but did not add it to avoid unnecessary storage and write overhead.

2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.

Two identical punch-in requests may both pass the initial attendance check. However, the unique compound index allows only one record for the same employee and date. The other insert raises DuplicateKeyError, which the code catches and converts into HTTP 409. This prevents duplicate daily attendance records.

3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?

The leaderboard uses MongoDB's $rank to assign equal ranks to employees with the same total late minutes. It filters by rank after ranking, so employees tied at the limit are included. Results are then sorted by late minutes and employee code.

4. **Headcount.** How does your department summary make sure employees with zero logs are counted?

The department summary starts from employees and looks up their attendance. Employees without logs receive zero-valued summaries, while employees who joined after the selected month are excluded.

5. **One thing you would change** if this had to serve 100x the data.

For 100 times more data, I would use query execution statistics and load tests to identify bottlenecks, then optimize indexes and aggregation pipelines.
