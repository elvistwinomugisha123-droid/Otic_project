# Website Performance Issues

## Symptoms

- Web application responding slowly (page load exceeding 5 seconds)
- Intermittent HTTP 502 Bad Gateway, 503 Service Unavailable, or 504 Gateway Timeout errors
- Users reporting browser timeouts or spinning loading indicators
- High latency on specific API endpoints while others respond normally
- Static assets (CSS, JS, images) loading slowly or failing to load
- Application works for some users but not others (partial outage)
- Increased error rates visible in monitoring dashboards

## Diagnosis Steps

1. Run `status_check` on `web-server`, `app-server`, and `database-server` to verify all layers of the stack are operational
2. Run `server_metrics` on `web-server` and `app-server` to check CPU, memory, and network utilization
3. Run `log_search` for "timeout", "502", "503", "504", or "slow query" in the last hour
4. Check if the performance degradation correlates with a recent deployment or configuration change
5. Check if traffic volume is abnormally high compared to baseline — could indicate a traffic spike, marketing campaign, or DDoS attack
6. Verify the database connection pool is not exhausted (look for "connection pool" or "too many connections" in logs)
7. Check if any background jobs or batch processes are consuming excessive resources
8. Test specific endpoints to determine if the slowness is global or isolated to certain routes

## Resolution Steps

1. **Web server CPU above 90%:** Check for runaway worker processes. If nginx, verify `worker_connections` is set appropriately for traffic volume. Consider enabling horizontal auto-scaling if available
2. **App server memory above 85%:** Check for memory leaks in the application — look for steadily increasing memory usage over time. Perform a rolling restart of app server instances to reclaim memory without downtime
3. **Slow database queries found:** Identify the specific slow query from logs. Check if required indexes exist. Add missing indexes or optimize the query. As a temporary measure, increase the database connection pool size to handle the backlog
4. **502/503 errors from reverse proxy:** Check if upstream app server instances are healthy and accepting connections. Verify reverse proxy (nginx/HAProxy) configuration — upstream timeouts may be too short. Restart the reverse proxy if configuration is correct but errors persist
5. **Traffic spike detected:** Enable rate limiting on the reverse proxy to protect the application. If auto-scaling is configured, verify it has triggered. Manually scale horizontally if needed
6. **Recent deployment caused the issue:** Roll back to the previous version immediately using the deployment pipeline. Investigate the problematic release in a staging environment before redeploying
7. **Connection pool exhausted:** Increase pool size temporarily. Identify and fix connection leaks — queries that open connections without closing them. Restart the application to release leaked connections

## Escalation Criteria

- Application is completely unresponsive for more than 5 minutes with no recovery after restarts
- Database corruption is suspected (data inconsistencies, failed integrity checks)
- DDoS attack is confirmed — requires infrastructure-level mitigation (CDN, WAF rules)
- Performance issue persists after rolling back the most recent deployment
- Data loss may have occurred due to failed write operations during the outage
