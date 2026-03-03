# Disk Usage Cleanup

## Symptoms

- Monitoring alerts indicating disk usage exceeding 85% threshold
- Applications failing to write files, logs, or temporary data
- Database errors with messages like "no space left on device" or "could not extend file"
- Slow system performance caused by low disk space (swap thrashing, filesystem journal full)
- Backup jobs failing with disk space errors
- Docker containers failing to start due to insufficient overlay storage
- Log files growing uncontrollably in size

## Diagnosis Steps

1. Run `server_metrics` to confirm current disk usage percentages across all servers
2. Run `log_search` for "disk", "no space", or "filesystem" errors in the last 24 hours
3. Identify which partition is full — common partitions to check:
   - `/` (root) — OS and application binaries
   - `/var` — variable data including logs and mail
   - `/var/log` — system and application logs (often the culprit)
   - `/tmp` — temporary files
   - `/data` — application data, databases
   - `/var/lib/docker` — Docker images, containers, and volumes
4. Determine if the growth was sudden (log flood, runaway process) or gradual (capacity planning needed)
5. Run `status_check` on affected services to see if any have stopped or degraded due to disk pressure
6. Identify the largest files and directories consuming space

## Resolution Steps

1. **Clear temporary files:** Remove files older than 7 days from `/tmp`. Command: `find /tmp -type f -mtime +7 -delete`
2. **Rotate and compress logs:** Archive log files older than 3 days with gzip compression. Delete compressed archives older than 30 days. Verify logrotate configuration is active and properly scheduled
3. **Remove package manager caches:** Clean apt cache (`apt-get clean`) or yum cache (`yum clean all`). This can free several GB on systems that have not been cleaned recently
4. **Docker cleanup (if applicable):** Remove unused images (`docker image prune -a`), stopped containers (`docker container prune`), and unused volumes (`docker volume prune`). Warning: verify no important data is in unnamed volumes before pruning
5. **If `/var/log` is full:** Identify which application is producing excessive logs. Check if log rotation is configured. Truncate the largest active log file as an emergency measure: `truncate -s 0 /var/log/large-log-file.log`. Fix the root cause by adjusting log verbosity or adding proper rotation
6. **If `/data` is full:** Identify the largest files and directories. Coordinate with application owners before removing anything — data files may be critical. Consider archiving old data to external storage
7. **After cleanup:** Verify disk usage has dropped below 80%. Confirm affected services have recovered by running `status_check` again. Set up monitoring alerts at 75% to catch issues earlier

## Escalation Criteria

- Disk usage above 95% on the root partition with no obvious files safe to remove
- Database partition is full (requires DBA coordination to archive or truncate tables safely)
- Disk shows SMART errors or hardware degradation signs (imminent drive failure)
- Cleanup provides only temporary relief — disk fills again within 24 hours (indicates a persistent leak or misconfiguration)
- Data loss may have already occurred due to write failures during the full-disk period
