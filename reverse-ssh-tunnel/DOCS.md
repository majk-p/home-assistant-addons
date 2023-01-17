# Home Assistant Add-on: Reverse SSH tunnel

## Installation

Follow these steps to get the add-on installed on your system:

1. This add-on is only visible to "Advanced Mode" users. To enable advanced mode, go to **Profile** -> and turn on **Advanced Mode**.
2. Navigate in your Home Assistant frontend to **Supervisor** -> **Add-on Store**.
3. Find the "Reverse SSH Tunnel" add-on and click it.
4. Click on the "INSTALL" button.

## How to use

This addon has one purpose - initialize reverse ssh tunnel connection to remote server to externalize it using reverse proxy.  

### SSH Server Connection

Reverse tunel details

## Configuration

Add-on configuration:

```yaml
authorized_keys:
  - "ssh-rsa AKDJD3839...== my-key"
password: ''
apks: []
server:
  tcp_forwarding: false
```

### Option: `apks`

## Externalization using reverse tunnel

TODO


## Support

TODO