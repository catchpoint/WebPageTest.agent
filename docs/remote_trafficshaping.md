# Remote traffic-shaping
wptagent supports configuring an external FreeBSD network bridge for traffic-shaping.  This is particularly useful for mobile device testing where the devices can be connected to a WiFi access point and the access point is connected through a FreeBSD bridge to get to the network.

i.e. mobile devices <--> WiFi Access Point <--> FreeBSD bridge <--> Internet

In this configuration the mobile devices are given static IP addresses and the FreeBSD bridge is pre-configured with 2 dummynet pipes for each IP address (one for inbound and one for outbound traffic).  The root account needs to allow for cert-based ssh access from the test machine (with the cert installed in authorized_keys).

Passing the agent a "--shaper remote" command-line flag you give it the IP address of the FreeBSD bridge as well as the pipe numbers for inbound and outbound traffic for the device (see below).  At test time the agent will ssh to the bridge and adjust the settings for the device as needed.

## Basic setup

You can probably use any device with two network interfaces.

* Install FreeBSD
* Optional: Set you keyboard layout by running `kbdmap` and adding the result output (i.e. `keymap="de.noacc.kbd"`) to `/etc/rc.conf`. 
* Enable sshd by adding `sshd_enable="YES"` to `/etc/rc.conf`
* Enable SSH root login by adding `PermitRootLogin yes` to `/etc/ssh/sshd_config`
* Install your SSH key(s) by adding the public keys to `/root/.ssh/authorized_keys`
* Make sure dummynet is loaded on startup by adding the following lines to `/boot/loader.conf`:
```
if_bridge_load="YES"
dummynet_load="YES"
```

## Bridge setup

Check the names of your network interfaces by running `ifconfig` in this documentation they are called `re0` and `re1`, so you need change this to you actual interface names in all code below.

The WiFi access point is connected to `re0`.
The main network is connected to `re1`. We want re1 to get an IP via DHCP.

Add the following lines to `/etc/rc.conf`:
```
#BRIDGE CONFIG. re0 and re1 are both ethernet devices
#  re1 is connected to main network
cloned_interfaces="bridge0"
ifconfig_bridge0="addm re0 addm re1 up"
# use dhcp for the ethernet device linked to the actual router (re1)
ifconfig_re1="DHCP up"
ifconfig_re0="up"

#IPFW Config (includes pipe definitions used for traffic shaping)
firewall_enable="YES"
firewall_type="open"
firewall_script="/etc/ipfw.rules"
```

If you **don't** want to use DHCP but a static IP for the bridge, you need to exchange the line `ifconfig_re1="DHCP up"` by a configuration like this:
```
ifconfig_re1="inet 192.168.1.3 netmask 255.255.255.0"
defaultrouter="192.168.1.1"
nameserver 192.168.1.1
```

## Dummynet pipe setup
The last line in `/etc/rc.conf` attempts to load ipfw rules from `/etc/ipfw.rules` which should include the pipe definitions that can be used for actual traffic shaping:

```
subnet=192.168.201
interface=re0

ipfw -q flush
ipfw -q pipe flush

# SSH traffic to bypass shaping
ipfw add skipto 60000 proto tcp src-port 22
ipfw add skipto 60000 proto tcp dst-port 22
# disable bootstrap traffic from outside to bridged devices
ipfw add deny ip from any to any bootps via re0

# Static pipes assigned by IP address to the $subnet.x subnet
for i in `seq 2 9`
do
  ipfw pipe $i config delay 0ms noerror
  ipfw pipe 30$i config delay 0ms noerror
  ipfw queue $i config pipe $i queue 100 noerror mask dst-port 0xffff
  ipfw queue 30$i config pipe 30$i queue 100 noerror mask src-port 0xffff
  ipfw add queue $i ip from any to $subnet.$i out xmit $interface
  ipfw add queue 30$i ip from $subnet.$i to any out recv $interface
done
for i in `seq 10 90`
do
  ipfw pipe $i config delay 0ms noerror
  ipfw pipe 3$i config delay 0ms noerror
  ipfw queue $i config pipe $i queue 100 noerror mask dst-port 0xffff
  ipfw queue 3$i config pipe 3$i queue 100 noerror mask src-port 0xffff
  ipfw add queue $i ip from any to $subnet.$i out xmit $interface
  ipfw add queue 3$i ip from $subnet.$i to any out recv $interface
done

ipfw add 60000 pass all from any to any
```

First, make sure that you that you correctly define the variables:
* `$interface` (in this case `re0`) is the interface name connected to the WiFi access point
* `$subnet` (in this case `192.168.201`) is the subnet of all mobile devices with static IPs

The logic in this file first allow unshaped traffic for SSH access and prevents any bootstrap traffic (e.g. for DHCP) from outside to the mobile devices.

The main logic is in the loop where it sets up pipes 2-90 and 302-390 as matched pairs and assigns each one of them to traffic going to each of the static IP addresses 192.168.201.2 through 192.168.201.90.

Pipes 2-90 are the inbound pipes for each of the phones and 302-390 are the outbound pipes. This allows to keep the configuration to be unchanged for up to 89 devices.

## WPT agent setup

Make sure each agent that uses external traffic shaping has it's ssh key installed on the FreeBSD bridge, so it can access the bridge with root privileges via SSH.

Assign a static IP in the subnet defined in the pipe setup to the **testing device** whose traffic should be shaped (not the agent itself when using mobile devices!).

Start the WPT agent with the argument `--shaper remote,<bridge address>,<down pipe>,<up pipe>`.

*Example:* If the hostname your free BSD bridge is `wpt-trafficshaper` and the static IP of the mobile device is `192.168.201.5`, the IPFW configuration automatically assigned the pipes `5` and `305` to `192.168.201.5` for inbound and outbound traffic.
Therefore you need to start the wpt agent with `--shaper remote,wpt-trafficshaper,5,305`
