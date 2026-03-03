# VPN Troubleshooting

## Symptoms

- User cannot establish a VPN connection (connection attempt fails or times out)
- VPN connects but drops frequently after a few minutes
- VPN is connected but internal resources (file shares, intranet, internal apps) are unreachable
- Extremely slow performance when connected to VPN
- Authentication failures or "invalid credentials" errors when connecting
- VPN client shows "certificate expired" or "certificate validation failed"
- DNS resolution fails for internal hostnames while connected to VPN

## Diagnosis Steps

1. Confirm which VPN client and version the user is running (OpenVPN, WireGuard, Cisco AnyConnect)
2. Ask if the issue started after a recent software update, OS patch, or network change
3. Run `status_check` on `vpn-gateway` to verify the VPN server is operational
4. Run `server_metrics` on `vpn-gateway` to check CPU utilization and network throughput
5. Run `log_search` for "vpn" or "authentication" errors in the last 2 hours
6. Ask user to verify their internet connection works without VPN (can they browse external websites?)
7. Check if the user's VPN certificate or credentials have expired
8. Verify the user is not connecting from a restricted network (hotel, airport, corporate guest WiFi that blocks VPN ports)
9. Check if the VPN gateway has reached its maximum concurrent connection limit

## Resolution Steps

1. **VPN server is down:** Restart the `vpn-gateway` service and monitor for 5 minutes. Verify connections are being accepted by checking the service logs
2. **Authentication failure:** Verify user credentials are correct. If password recently changed, ensure the VPN client is using the new password. Reset password if needed. Check certificate expiration date — renew if expired
3. **Connected but no internal access:** Check split-tunnel configuration. Verify DNS settings point to internal DNS servers (10.0.0.2 and 10.0.0.3). Test with `nslookup internal-hostname 10.0.0.2` to confirm DNS resolution
4. **Frequent disconnects:** Check MTU settings — recommend setting MTU to 1400 to avoid fragmentation. Disable Wi-Fi power saving mode. Check for competing network interfaces (disable secondary NICs while on VPN)
5. **Slow VPN performance:** Check server load — if CPU above 80% or network throughput is saturated, route new connections to the backup VPN gateway (vpn-gateway-02). Check if the user is on a bandwidth-limited connection
6. **Blocked VPN ports:** If the user is on a restricted network, try switching to TCP port 443 (HTTPS) which is rarely blocked. Enable VPN obfuscation if available

## Escalation Criteria

- VPN gateway service will not restart after 2 attempts
- More than 10 users cannot connect simultaneously (indicates server-side infrastructure failure)
- Certificate authority infrastructure issue suspected (mass certificate validation failures)
- Network routing tables appear corrupted (traffic is being routed incorrectly after VPN connection)
- Suspected security incident — unauthorized VPN connections from unknown IP addresses
