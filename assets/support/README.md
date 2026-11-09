# Remote support assets

`ssh-authorized-keys` holds the public keys authorized when
**Settings > System > Remote support** is enabled. The matching private key for
development lives only on the maintainer workstation
(`~/.ssh/pideck-tv-support`) and is never shipped in the update bundle.
