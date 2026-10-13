# Portfolio presentation

## Short project description
JobFlow is a multi-user job application tracker built with Flask and PostgreSQL. It supports authentication, email verification, per-user CRUD operations, filtering and pagination, analytics visualizations, and CSV data import/export.

## Suggested resume bullets (use only after personally verifying the behavior)
- Built a Flask web application backed by PostgreSQL to manage job applications with authenticated, user-scoped CRUD operations.
- Implemented search, status filters, sorting, pagination, and SQL-based dashboard metrics.
- Created an analytics dashboard using Chart.js to visualize application outcomes, monthly activity, and location breakdowns.
- Added CSV import/export workflows with input validation and duplicate detection.
- Implemented password hashing, email verification, password reset, and profile management.

## Suggested interview walkthrough
1. Explain the relational model (`users` and `jobs`) and how `user_id` scopes application data.
2. Demonstrate a job's full lifecycle: create, search/filter, edit, view details, delete.
3. Show analytics and explain the SQL aggregation queries.
4. Demonstrate CSV import/export and describe validation and duplicate handling.
5. Explain the connection pool and the importance of closing/returning database connections.
6. Discuss what you would add before production: CSRF, rate limiting, Alembic migrations, test coverage, monitoring and deployment.
