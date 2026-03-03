# Service Restart & Diagnosis

## Symptoms

- A monitored service has stopped running or is in a failed state
- Service is running (process exists) but is not accepting connections on its expected port
- Service restarts repeatedly in a crash loop (starts, crashes within seconds, restarts, crashes again)
- Dependent services are failing because an upstream service they rely on is down
- Monitoring shows the service as "down" or "unreachable"
- Users report functionality that depends on a specific backend service is unavailable

## Diagnosis Steps

1. Run `status_check` on the reported service to confirm its current operational state
2. Run `log_search` with the service name to find crash reasons, error messages, or stack traces
3. Run `server_metrics` for the host running the service to check if resource exhaustion (CPU, memory, disk) caused the failure
4. Check if the service's configuration file was recently modified — misconfiguration is a common cause of startup failures
5. Check if dependent services are healthy:
   - Database (PostgreSQL, MySQL) — is the DB accepting connections?
   - Message queue (RabbitMQ, Redis) — is the queue service running?
   - Cache layer (Redis, Memcached) — is the cache accessible?
6. Verify the service's expected port is not being held by another process (`lsof -i :PORT` or `netstat -tlnp | grep PORT`)
7. Check systemd journal for the service: `journalctl -u service-name --since "1 hour ago" --no-pager`
8. If crash looping, check the exit code — OOM killer (exit code 137) indicates memory exhaustion

## Resolution Steps

1. **Service is stopped (clean shutdown):** Attempt a standard restart: `systemctl restart service-name`. Monitor logs for 2 minutes to confirm it stays running and begins accepting connections
2. **Service is in a crash loop:** Check the last 50 log lines for the root cause error. Common causes:
   - Missing or invalid configuration file — restore from backup or fix the syntax error
   - Missing dependency (library, module, binary) — reinstall the required package
   - Port already in use — find and stop the conflicting process
   - Database connection refused — fix the DB first, then restart this service
3. **Resource exhaustion caused the failure:** Address the resource issue first:
   - Disk full → Follow the Disk Usage Cleanup runbook
   - Memory exhausted → Identify and kill the memory-hogging process, then restart the service
   - CPU saturated → Identify runaway processes, reduce load, then restart
4. **Dependency is down:** Always restart dependencies in the correct order:
   - Database first → wait for it to accept connections
   - Cache/queue second → wait for healthy status
   - Application service last → verify it connects to all dependencies
5. **Port conflict:** Identify the conflicting process: `lsof -i :PORT`. Terminate it if it is a stale/orphaned process. If it is a legitimate process, resolve the port assignment conflict in configuration
6. **After restart:** Verify the service is healthy:
   - Run `status_check` to confirm it shows as healthy
   - Check that the service responds on its expected port
   - Verify dependent services have recovered
   - Monitor logs for 5 minutes to ensure stability

## Escalation Criteria

- Service will not start after 3 restart attempts with different troubleshooting steps applied
- Root cause is a code bug that requires a developer to fix (not a configuration or infrastructure issue)
- Service failure has caused data corruption or data loss
- Multiple unrelated services are failing simultaneously — suggests a broader infrastructure problem (host failure, network partition, storage failure)
- The service is crash looping due to a bad deployment and rollback is not available
