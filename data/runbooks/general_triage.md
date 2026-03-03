# General IT Triage Guide

## Symptoms

- User reports vague or ambiguous issues ("system is slow", "something is broken", "it's not working")
- No specific service, application, or error message identified
- Multiple symptoms that could point to different root causes
- User is unsure when the issue started or what changed
- Intermittent problems that are difficult to reproduce

## Diagnosis Steps

1. Ask the user to describe exactly what they were doing when the issue started
2. Confirm which application, service, or device is affected
3. Ask if the issue is consistent or intermittent
4. Determine if the issue started after a recent change (software update, config change, new hardware)
5. Check if other users are experiencing the same problem — single-user issues suggest client-side causes
6. Run `status_check` on all critical services to identify any outages or degraded services
7. Run `server_metrics` to check for resource exhaustion:
   - CPU usage above 90% indicates compute bottleneck
   - Memory usage above 85% suggests memory pressure or leaks
   - Disk usage above 90% can cause write failures and application crashes
   - Load average above 4.0 on a 4-core system indicates saturation
8. Run `log_search` for recent ERROR or CRITICAL entries in the last 30 minutes
9. Cross-reference findings: does the timeline of errors match when the user first noticed the problem?

## Resolution Steps

1. **If a specific service is down:** Refer to the Service Restart & Diagnosis runbook (`service_restart_diagnosis.md`)
2. **If disk is full:** Refer to the Disk Usage Cleanup runbook (`disk_usage_cleanup.md`)
3. **If network or VPN issue:** Refer to the VPN Troubleshooting runbook (`vpn_troubleshooting.md`)
4. **If web application is slow or returning errors:** Refer to the Website Performance runbook (`website_performance.md`)
5. **If email-related:** Refer to the Email Sync Issues runbook (`email_sync_issues.md`)
6. **If no system-level issue is found:** The problem is likely client-side. Advise the user to:
   - Restart the affected application
   - Clear browser cache and cookies if it is a web application
   - Reboot their machine
   - Try from a different network to rule out local network issues
7. **If the issue persists after basic client-side steps:** Gather all diagnostic output collected so far and escalate

## Escalation Criteria

- Issue affects more than 5 users simultaneously (likely infrastructure-level)
- Root cause cannot be identified after checking all metrics, logs, and service statuses
- Issue involves suspected data loss or data corruption
- Security breach indicators detected (unauthorized access attempts, unusual login patterns)
- Hardware failure suspected (disk SMART errors, memory ECC errors, kernel panic logs)
- Problem recurs within 24 hours of applying a fix
