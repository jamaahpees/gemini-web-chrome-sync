# Deployment checklist

- [ ] Receiver unit/integration tests pass, including backend failures and concurrent snapshots.
- [ ] Extension tests pass; inspect Chrome service worker without a user's real account.
- [ ] Pairing succeeds exactly once; invalid/expired codes fail without disclosing details.
- [ ] A synthetic cookie change reaches receiver; acknowledgement matches sequence.
- [ ] Two separate profiles/targets cannot overwrite each other.
- [ ] Unauthenticated HTTP requests return no secrets.
- [ ] Existing 9router and 10router health unchanged.
- [ ] Existing Tailscale Funnel 443 configuration retained; sync HTTPS8443 absent from AllowFunnel.
- [ ] Backend rotation disabled/coordinated; verify supported mechanism against source.
- [ ] Credentials, DB and service config mode0600/private directory0700.
- [ ] Latest verified SQLite backup available before updating live backend settings.
- [ ] Operator generates pairing code locally, never in public URLs or logs.
- [ ] Real Chrome installation/pairing is completed by user with explicit host permission consent.
- [ ] Actual Google cookie refresh and Argon generation distinguished from synthetic transport tests.

## Rollback

Disable the receiver service and private Serve8443 route. Disable/pause extension sync. Restore the previous backend config from its verified backup if rotation was changed. Existing cookie snapshots should not be restored blindly: an old Google session may already have been revoked. Do not delete Docker volumes, rollback containers, or unrelated work.
