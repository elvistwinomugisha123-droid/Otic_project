# Email Sync Issues

## Symptoms

- Users are not receiving new emails (inbox appears stale or stuck)
- Sent emails do not appear in the sent folder
- Email client displaying sync errors, connection failures, or authentication errors
- Calendar invites and meeting updates not syncing across devices
- Delayed email delivery — messages arriving more than 15 minutes after being sent
- Attachment downloads failing or timing out
- Out-of-office auto-replies not being sent
- Email search not returning recent messages

## Diagnosis Steps

1. Run `status_check` on `email-server` and `smtp-relay` to verify both services are operational
2. Run `server_metrics` on `email-server` to check disk usage (mailbox storage) and memory usage
3. Run `log_search` for "email", "smtp", "imap", "dovecot", or "postfix" errors in the last 4 hours
4. Determine the scope — is the issue affecting one user or multiple users?
   - Single user: likely account-specific (quota, credentials, client config)
   - Multiple users: likely server-side (service outage, resource issue, DNS)
5. Check if the mail queue has a backlog of undelivered messages (look for "queue" or "deferred" in logs)
6. Verify DNS MX records are resolving correctly — incorrect MX records will prevent inbound email delivery
7. Check if the email server's SSL/TLS certificate is valid and not expired
8. For single-user issues: check if the user's mailbox quota has been exceeded

## Resolution Steps

1. **Email server is down:** Restart the email service (Dovecot for IMAP, Postfix for SMTP). Monitor the mail queue — messages should begin delivering within minutes. Watch logs for any errors during startup
2. **Disk full on email server:** Run disk cleanup on the mail server. Archive mailboxes of users who have exceeded their quota. Increase disk allocation if cleanup is insufficient. See Disk Usage Cleanup runbook for detailed steps
3. **Single user affected:**
   - Check the user's mailbox quota — if exceeded, have them delete old emails or increase their quota
   - Verify the account is not locked or disabled in the directory service
   - Re-create the email profile on the user's client application (Outlook, Thunderbird, mobile)
   - Test with webmail — if webmail works but the client does not, the issue is client-side configuration
4. **SMTP relay is down:** Restart the `smtp-relay` service. Check outbound firewall rules — port 25 (SMTP) and port 587 (submission) must be open. Verify relay authentication credentials have not expired. Check if the relay IP has been blacklisted (use an online blacklist checker)
5. **Mail queue backlogged:** Inspect the queue for patterns — are messages stuck to a specific destination domain? Flush the queue after fixing the underlying cause. Check for spam or bounce loops that may be flooding the queue with retries
6. **DNS MX record issue:** Correct the MX records in the DNS zone file. Allow up to 1 hour for DNS propagation. Verify with `dig MX yourdomain.com` from an external DNS resolver. In the meantime, direct senders to use the mail server IP address directly if urgent

## Escalation Criteria

- Email server will not restart after 2 attempts
- Mail queue contains more than 10,000 undelivered messages (indicates a systemic delivery problem)
- Suspected email compromise or unauthorized access (unfamiliar sent emails, forwarding rules the user did not create)
- Email delivery failure is affecting external communication (clients, vendors) for more than 1 hour
- SSL/TLS certificate has expired and renewal process is failing
- Mail server IP address has been blacklisted by major email providers (Gmail, Microsoft)
