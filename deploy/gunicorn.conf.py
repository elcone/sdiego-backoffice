bind = "127.0.0.1:8002"
workers = 2
forwarded_allow_ips = "127.0.0.1"
accesslog = "-"
errorlog = "-"
# No URLs, query strings, headers or bodies (including password-reset tokens).
access_log_format = "%(h)s %(m)s %(s)s %(L)s"
