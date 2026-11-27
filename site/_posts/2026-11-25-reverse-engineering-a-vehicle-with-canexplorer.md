---
layout: post
title: "From the OBD port to a DBC file: reverse engineering a vehicle with CANExplorer"
description: "Connect to a vehicle's CAN bus, find what its messages mean, and turn what you find into a signal database you can share on dbchub."
author: dbchub
tags: [canexplorer, reverse-engineering, dbc, tutorial]
permalink: /blog/reverse-engineering-a-vehicle-with-canexplorer/
---

A modern car is a network of computers. They talk on CAN buses, usually at 500 kbit/s. Every few
milliseconds the engine controller reports its speed, the ABS module reports four wheel speeds, and the body
controller reports which doors are open. Nothing is encrypted in most of this traffic, but almost none of it
is documented. The bus carries bytes like `500#00 01 1A 2C 00 00 00 00`. Turning those bytes into
`DoorFrontLeft = Open` and `VehicleSpeed = 67.0 km/h` takes a map, and that map is a **signal database**, most
often a Vector **DBC** file.

Manufacturers keep their DBC files to themselves. If you want one for your car, you usually have to build it
by watching the bus, changing one thing at a time, and noting which bits move. This guide walks through the
whole process with [CANExplorer](https://github.com/AKJ7/CANExplorer), a free, cross-platform CAN tool:

1. connect a laptop to the vehicle,
2. record traffic and get oriented,
3. find signals with the notch, markers, reaction capture, reference signals and automatic analysis,
4. turn what you find into a DBC file, and
5. share the result on dbchub so the next person doesn't start from zero.

You don't need a car to follow along. [Trying it without a vehicle](#trying-it-without-a-vehicle) shows how to
make realistic traffic from a database and practise on that first.

> **NOTE** The screenshots in this post come from a fictional **test car**: realistic traffic generated from a
> database whose answers we already know, recorded through a virtual bus. That way every number you see is
> reproducible, and nobody's real vehicle data is published. The script that makes them is in the dbchub repository
> under `tools/blog/canexplorer/`.
{: .callout .note}

* TOC
{:toc}

## Before you begin: rules of the road

> **WARNING** Only connect to a vehicle you own or are explicitly allowed to work on. A CAN bus has no
> authentication. If you send a frame, the bus accepts it, and the wrong frame on the wrong bus can trigger
> warning lamps, store fault codes, or make a module do something you didn't intend. **Do everything in this
> guide in listen-only mode, with the vehicle parked**, unless a step clearly says otherwise. Never send frames
> while the vehicle is moving.
{: .callout .warning}

Most of reverse engineering is passive. You watch, you change something in the cabin, you watch again. Only
two optional steps in this guide send anything: polling OBD-II values as a reference, and replaying one frame
you observed to confirm a finding. Both are marked.

## What you need

| Item | Notes |
|---|---|
| A laptop | Linux, Windows or macOS. Linux with SocketCAN is the least trouble. |
| A CAN adapter | Anything [python-can](https://python-can.readthedocs.io/) supports. See the table below. |
| A way onto the bus | An OBD-II cable or breakout for the diagnostic port, or a tap at a module connector (see [Finding the bus](#finding-the-bus)). |
| CANExplorer | Free; installers for every platform. |
| Patience and a notebook | Write down what you did and when you did it. Reverse engineering is mostly bookkeeping. |

Adapters known to work with CANExplorer, from cheapest to most capable:

| Adapter | CANExplorer interface | Good for |
|---|---|---|
| CANable / candleLight (gs_usb firmware) | *SocketcanBus* on Linux, *GsUsbBus* elsewhere | The usual cheap choice; full raw access |
| CANable / USBtin (slcan firmware) | *slcanBus* | Any OS, no driver; give the serial port as the channel |
| Macchina M2, ESP32RET (GVRET firmware) | *GvretBus* | Two buses on one device, or WiFi |
| PEAK PCAN-USB, Kvaser, Vector | *PcanBus*, *KvaserBus*, *VectorBus* (or SocketCAN on Linux) | Professional tools with reliable timestamps |
| ELM327 / OBDLink | *Elm327Bus* | **Diagnostics only.** These are built for OBD requests and can't keep up with a busy bus. Fine for the OBD-II page, poor for sniffing. |

> **NOTE** If you already own an ELM327 dongle, it's enough to read trouble codes and live OBD-II values. It
> isn't enough for this guide: you need to see *every* frame on the bus, and ELM327 adapters drop frames under
> load. Buy a gs_usb or slcan adapter; they cost less than a tank of fuel.
{: .callout .note}

## Installing CANExplorer

Download an installer for your platform from the
[releases page](https://github.com/AKJ7/CANExplorer/releases): a Windows setup `.exe`, a Linux AppImage or
`.deb`, or a macOS `.dmg`. On Linux you can also use the snap:

```bash
sudo snap install canexplorer
sudo snap connect canexplorer:can-bus   # SocketCAN adapters: can0, vcan0 …
sudo snap connect canexplorer:raw-usb   # USB adapters: candleLight/gs_usb, slcan, GVRET …
```

Or run it from source with [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/AKJ7/CANExplorer.git
cd CANExplorer
uv sync
uv run canexplorer
```

## Finding the bus

Every car sold in the US since 1996, and in the EU since the early 2000s, has an **OBD-II port** under the
dashboard, usually within reach of the driver's knees. Since 2008, US cars have been required to use CAN on it. Its
16 pins are standardised by SAE J1962:

{% include blog/obd2-port.svg %}

The ones that matter here:

| Pin | Signal |
|---|---|
| 6 | CAN high (ISO 15765-4, the diagnostic CAN) |
| 14 | CAN low |
| 4, 5 | Chassis ground, signal ground |
| 16 | Battery +12 V (always on, so don't short it) |

Other pins are left to the manufacturer. Some use them for more buses, for example pin 1 for GM's single-wire
CAN, or pins 3 and 11 for a medium-speed CAN on some Fords. A breakout box with LEDs or an oscilloscope will show
which pins carry traffic.

> **NOTE: the gateway problem.** On many older vehicles, pins 6 and 14 lead straight onto the powertrain bus,
> and you'll see hundreds of messages the moment the ignition is on. Many newer vehicles put a **gateway** behind
> the OBD port. The gateway passes diagnostic requests and answers but not the normal broadcast traffic, so the
> bus looks silent or nearly silent. If that happens, the traffic you want is behind the gateway. You can reach
> it at a module connector (the radio, the instrument cluster, a body controller) or at the gateway's own
> harness. Use a proper tap or a breakout harness, never a cut wire, and look up the connector pinout for your
> model first.
{: .callout .note}

## Bringing up the adapter

### Linux and SocketCAN

Most USB adapters show up as a network interface (`can0`). Set the bitrate and bring the interface up **in
listen-only mode**, so the adapter never acknowledges or sends a frame:

```bash
sudo ip link set can0 type can bitrate 500000 listen-only on
sudo ip link set can0 up
```

500 kbit/s is the most likely rate for the OBD port and the powertrain bus. Body and comfort buses often run at
125 kbit/s. Newer vehicles may use CAN FD; for those, add `dbitrate 2000000 fd on` (the data rate depends on the
vehicle).

> **TIP** If the bitrate is wrong, the bus doesn't look empty. It shows error frames. That gives you a quick
> test: connect, and if [Bus health](#check-the-bus-health) fills with errors, take the interface down, try the
> next common rate (500k → 250k → 125k → 1M) and look again. In listen-only mode, a wrong guess doesn't disturb
> the vehicle.
{: .callout .tip}

### Windows and macOS

Install your adapter's driver (PEAK PCAN-Basic, Kvaser CANlib, Vector XL), or use an slcan or gs_usb adapter
that needs none. Set the bitrate in CANExplorer's connection dialog. Where the interface has one, tick its
**Listen only** option.

## Connecting in CANExplorer

Start CANExplorer, then click **New connection** (<kbd>Ctrl</kbd>+<kbd>N</kbd>).

![The New connection dialog filled in for a SocketCAN adapter on can0 at 500 kbit/s]({{ '/assets/img/blog/canexplorer/new-connection.png' | relative_url }})

A SocketCAN adapter on `can0`, 500 kbit/s, no protocol decoder yet.
{: .caption}

| Field | What to enter |
|---|---|
| Connection name | Something you'll recognise later, such as `Golf-OBD` |
| Bus | **CAN** |
| Interface | *SocketcanBus*, *slcanBus*, *PcanBus*… whichever matches your adapter |
| Channel | `can0`, `/dev/ttyACM0`, `COM3`, `PCAN_USBBUS1`… **Detect** asks every installed driver for its channels |
| Protocol | **Raw** for now |
| Bitrate | 500000 (ignored for SocketCAN, which takes the rate from `ip link`) |

Click **OK**, then **Connect**. The connection's **Dashboard** opens, showing frame rate, bus load and the
busiest identifiers. Turn the ignition on (the engine doesn't need to run yet) and the numbers should start
moving.

### Check the bus health

Before you trust anything you see, open **Bus health**. It shows the controller's error state, error frames and
error counters over time. A healthy, correctly configured bus shows **no error frames** at all. If errors pile up,
the bitrate is wrong, CAN high and low are swapped, or you're on a pin with no CAN on it.

![Bus health: error frames and a climbing receive error counter, then a clean bus after the bitrate is fixed]({{ '/assets/img/blog/canexplorer/bus-health.png' | relative_url }})

What a wrong bitrate looks like: a listen-only adapter at 250 kbit/s on a 500 kbit/s bus sees only stuff errors. The receive error counter (REC) climbs through *error warning* to *error passive*, and the bus load stays at zero. After the interface is brought up again at 500 kbit/s, the errors stop and traffic appears.
{: .caption}

## Record first, analyse later

The single best habit in this work is to **record everything**. A recording can be replayed in CANExplorer as if
it were the live bus: every view, every reverse-engineering tool and every database works the same on it. So you
can capture in the car for ten minutes, then analyse at your desk for as long as you like.

Click **Record…** and choose a file name. **BLF** (`.blf`) is compact and widely supported. ASC, candump
`.log`, PEAK TRC, CSV, SQLite and MF4 work too. Then go through a deliberate script and write down the time of each
step:

1. ignition on, nothing touched, for 30 seconds (your **baseline**);
2. each door opened and closed, one at a time;
3. indicators left, right, hazards;
4. headlights, high beam, wipers;
5. brake pedal pressed and released a few times;
6. engine started, idle, a few blips of the throttle;
7. if you can do it safely and legally, a short drive with a passenger operating the laptop.

> **TIP** In the record dialog you can pick a **camera** and record a video next to the log
> (`drive.blf` → `drive.mp4`). When you replay the log, the video plays in step with the frames, so you can
> see *what was happening* when a byte changed. A phone pointed at the dashboard works fine.
{: .callout .tip}

To replay a recording, use **⋮ → Replay Log File…** (<kbd>Ctrl</kbd>+<kbd>O</kbd>). It opens as a new connection
with a playback bar for pausing, seeking, and replaying from 0.25× to 10× speed.

## Getting oriented: the overview

Open **Overview**. It shows one row per identifier, with its frame count, its **cycle time**, and its bytes, with
the bytes that just changed highlighted.

![The Overview of the test car: four identifiers with their counts, cycle times and changing bytes]({{ '/assets/img/blog/canexplorer/overview.png' | relative_url }})

The test car has four identifiers: two at 10 ms (`0C0`, `0E0`), one at 20 ms (`500`) and one at 100 ms (`600`). The orange bytes are the ones that just changed. Notice the last byte of `0C0` and `500` changing in every frame.
{: .caption}

Spend a few minutes just looking. Patterns start to show:

- **Cycle times group messages.** Messages every 10 ms are usually powertrain and chassis data (engine speed,
  wheel speeds, steering angle). 100 ms and 1000 ms messages are more often status: lights, doors, temperatures.
- **Messages that appear only now and then** are event-triggered: a button press, a door, a key fob.
- **Low identifiers win arbitration**, so safety-critical data tends to sit at low IDs.
- **The last byte or the last nibble often changes in every frame.** That's typically an alive counter or a
  checksum. You'll confirm it later.

**Filters** help once you know what to ignore. A display filter such as `100-1FF !123` shows only identifiers
`0x100` to `0x1FF` except `0x123`. The trace can also search for a data pattern.

## Finding signals

Open the **Reverse engineering** view of the connection. The list on the left shows every identifier, and the tabs
across the top are different tools for different questions. You'll use most of them on a typical vehicle.

![The Bits tab of 0x500 after a notch, with bytes 2 and 3 selected as a Motorola bit field]({{ '/assets/img/blog/canexplorer/reverse-engineering.png' | relative_url }})

After the notch, only bit 8 of `0x500` (outlined in orange) changed when the driver's door was opened, and the list shows **1 bit** next to `500`. Bytes 2–3 are selected as the Motorola field `500[23|16@0+]` with factor 0.01, which reads 91.73 km/h in the last frame.
{: .caption}

### Reading the bits

Select an identifier. The **Bits** tab shows its payload laid out the way a DBC file counts bits: one row per byte,
with bit 7 on the left.

- **Blue** cells change. The darker the blue, the more often the bit changes. Grey cells never changed.
- A cell flashes **orange** when its bit changes.
- The byte table underneath gives each byte's value range, number of distinct values, entropy and a histogram.

Different kinds of field change in recognisable ways:

| Pattern | Probably |
|---|---|
| The lowest bit changes every frame, the next every second frame, and so on (a staircase) | A counter |
| High bits slow, low bits noisy, moving together | A physical value: a speed, temperature or pressure |
| A single bit, rarely changing | A flag: a door, a switch, a lamp |
| A whole byte that looks like noise, changing every frame | A checksum or CRC, or (on secured buses) a MAC |
| A few values in a row, rarely changing | A state: gear, drive mode, wiper speed |

The **Activity** tab shows the same thing over the *whole* capture at once. Each row is a bit and each column is a
frame. Counters appear as diagonal stripes, constants as flat bands, toggles as columns of orange that come and
go, and multiplexers as whole regions switching on and off. It's the quickest way to see a message's structure at
a glance.

### Technique 1: the notch (find a button)

The notch answers "which bits does *this* button move?", even when dozens of other bits are changing on their own.

1. Let the traffic run for half a minute without touching anything, so every bit that moves by itself gets
   counted.
2. Press **Notch**. Every bit that has changed so far is dimmed.
3. Do the action once: open the driver's door, press the hazard switch.
4. The bits that change *now* are outlined in orange. The identifier list shows how many bits changed per
   identifier (**2 bits**), and **Only changed since the notch** hides the rest.

Press **Notch again** to separate two actions (left indicator, then right), and **Clear notch** to start over.

> **TIP** A slowly changing value can reach bits it didn't use before the notch. For example, the engine-speed
> high bits move when you rev the engine for the first time. Keep everything else as steady as you can, and repeat
> the action two or three times. The bits that belong to your action change *every* time.
{: .callout .tip}

### Technique 2: markers (repeat an action)

When the bus is too busy for a clean notch, use markers. In the **Markers** tab, type a label (*door opened*) and
press **Mark now** (<kbd>Ctrl</kbd>+<kbd>M</kbd>) each time you do the action. Five times is good. Then press
**Find changing bits**.

![Markers: bit 8 of 0x500 changed at all five markers and never otherwise]({{ '/assets/img/blog/canexplorer/reverse-markers.png' | relative_url }})

Five markers, five hits: bit 8 of `0x500` changed at every marker and never otherwise (score 1). The two bits of `0x600` below it changed near some markers by chance, but they also change on their own, so they score lower.
{: .caption}

The result ranks fields by how many markers they changed at (**Markers hit**), how often they change otherwise,
and what they changed between, such as `0 → 1`. A door switch hits 5 of 5 markers and never changes otherwise,
which makes it obvious. Markers also work while you replay a recording at normal speed, so you can mark events
afterwards from the video.

### Technique 3: capture reaction (baseline vs. action)

The **Reaction** tab does the same job in a fixed three-step order:

1. **Record baseline**: leave everything steady for a few seconds.
2. **Start action**: do the thing once (lock the doors with the fob, switch on the rear demister).
3. **Capture reaction**: CANExplorer compares the two windows.

![Capture reaction: switching on the headlights starts identifier 0x6A1 and flips bit 10 of 0x3B0]({{ '/assets/img/blog/canexplorer/reverse-reaction.png' | relative_url }})

Headlights switched on: a lamp status message `0x6A1` starts, and bit 10 of the switch message `0x3B0` goes from 0 to 1. The cluster message `0x420`, which kept sending unchanged, is left out.
{: .caption}

It lists identifiers that appeared only during the action (a module woke up and started talking), and runs of
bits that were stable during the baseline but changed during the action. Fields that were already moving on their
own, such as counters and live sensors, are left out. It never transmits.

### Technique 4: reference signals (find a value you can measure)

Buttons are easy. Physical values like vehicle speed, engine speed and coolant temperature are harder, because
you also need the **scaling**. If you can measure the value another way, CANExplorer can find it for you. In the
**Reference signals** tab, choose a reference:

- **A CSV file**, for example from a GPS logger: time in seconds in the first column, values in the others, and a
  header such as `speed [km/h]` that gives the unit.
- **OBD-II values**: CANExplorer polls standard values such as engine speed and vehicle speed through the
  diagnostic port while you record, and uses the answers as the reference.
- **A database signal** you already decoded, to find the same value in another message or on another bus.

> **WARNING** OBD-II polling **sends** diagnostic requests (to `7DF`). It's harmless on a healthy vehicle, and
> it's how every scan tool works, but it isn't listen-only. Switch the interface out of listen-only mode for this
> step, poll only on your own vehicle, and stop when you're done. CANExplorer leaves the request and response
> frames out of the analysis.
{: .callout .warning}

Press **Find matching fields**. CANExplorer tries every candidate field (8 and 16 bit, both byte orders, signed
and unsigned, plus everything the automatic analysis found) and ranks them by how closely they follow your
reference. It then fits a straight line to give you the **factor** and **offset**, and the **lag**.

![Reference signals: the GPS speed matches 500[23|16@0+] with correlation 1 and factor 0.01]({{ '/assets/img/blog/canexplorer/reverse-reference.png' | relative_url }})

A GPS log's speed against 186 candidate fields: `500[23|16@0+]` matches with correlation 1, factor 0.01, offset about 0 and no lag. The steering angle in `0E0` comes next, at −0.87: a coincidence of this drive, not a match.
{: .caption}

A correlation near 1.0 is a match. Here the vehicle speed from a GPS log turned out to be a 16-bit Motorola field in
`0x500` with a factor of 0.01: raw `6700` (`1A 2C`) means 67.00 km/h. **Use** copies the field, scaling and unit into the bit
field form.

If nothing correlates, the value may not be on this bus, or it didn't change enough during the recording. Drive
through a wider range of speeds and try again.

### Technique 5: automatic analysis (let the machine look first)

**Analyse all identifiers** goes through every message and classifies what it finds:

![Automatic analysis of the test car: values, counters, CRC-8 checksums, a flag and a multiplexer]({{ '/assets/img/blog/canexplorer/reverse-analysis.png' | relative_url }})

Every identifier analysed at once: CRC-8 SAE J1850 checksums and 4-bit alive counters in `0C0` and `500`, the door flag at `500[8|1@1+]`, a signed steering angle in `0E0`, and a multiplexer in `600` with the cell voltages behind it. The speed shows up as `500[21|14@0+]`, two bits short (see the note below).
{: .caption}

| Kind | What it means |
|---|---|
| **Checksum** | A byte that is a CRC-8 (several common polynomials), XOR or sum of the other bytes, with the parameters found |
| **Counter** | Steps by a fixed amount each frame and wraps: an alive counter |
| **Multiplexer** | A selector value. Grouping the frames by it makes the rest of the payload much calmer. Fields behind it are listed per value. |
| **Value** | Changes smoothly: a physical quantity. Marked signed when that gives a smoother curve. |
| **State** | A few values, rarely changing |
| **Flag** | A single bit |
| **Random (MAC?)** | Trailing bytes that look like noise: possibly a security MAC (AUTOSAR SecOC) or encrypted data |

> **NOTE** The analysis can only find bits that change. A value that never went negative during your recording
> is reported as unsigned, and a value that stayed below 256 is reported as 8 bits wide. In the screenshot above the
> test car never went faster than 163.83 km/h (raw 16383), so the top two bits of the speed never changed and the
> analysis found a 14-bit field. The reference signals and the plot below confirm the full 16 bits. Record while the vehicle
> goes through as much of its range as you can. The results are a strong first guess. You still check each one,
> and you name each one yourself.
{: .callout .note}

Checksums and counters matter even if you don't care about their values. Once you know which bits they occupy,
you can ignore them everywhere else, and anyone who later *transmits* that message needs to compute them
correctly.

### Technique 6: related signals (one value, many messages)

Gateways often re-broadcast the same quantity on several messages and buses, sometimes scaled or delayed. Wheel
speed is a classic example. **Find related signals** groups the fields that follow one another and gives the
factor, offset and lag between them. Once you've identified one member of a group, you've identified all of them.

![Related signals: the vehicle speed of 0x500 and its copy on 0x510 at factor 2, 50 ms later]({{ '/assets/img/blog/canexplorer/reverse-related.png' | relative_url }})

The vehicle speed of `0x500` and a copy on `0x510`, which the instrument cluster re-broadcasts in Intel byte order at half the resolution (factor 2) and about 50 ms later.
{: .caption}

### Defining and checking a field

However you found it, a candidate ends up in the **bit field** form of the Bits tab:

- Click the first bit, then Shift+click the last. Choose the **byte order** *before* selecting:
  - *Intel* (little endian): the low byte comes first.
  - *Motorola* (big endian): the high byte comes first. DBC files count a Motorola signal's start bit at its most
    significant bit.
- Tick **Signed** for two's complement values, and set **Factor**, **Offset** and **Unit**. The physical value is
  `raw × factor + offset`.

The form shows the field in DBC notation. `500[23|16@0+]` means start bit 23, 16 bits long, `0` = Motorola, `+` =
unsigned.

Now press **Plot**. This is the most important check in the whole process:

![The same 16 bits plotted twice: as Motorola a smooth speed curve, as Intel a band of noise]({{ '/assets/img/blog/canexplorer/signals.png' | relative_url }})

The same two bytes plotted twice. Read as Motorola (`500[23|16@0+]`, top) they give a smooth speed between 3.5 and 136.5 km/h. Read as Intel (`500[16|16@1+]`, bottom) the low byte becomes the high byte, and the curve jumps all over the range.
{: .caption}

- A **smooth curve** that rises when you accelerate and falls when you brake means the field is right.
- A curve that **jumps between two bands** usually means the byte order is swapped, or the field starts or ends one
  bit off.
- A curve that **jumps from very high to very low around zero** means the field is signed.
- A **sawtooth** is a counter, not a value.

## Building the database

### From findings to signals

There are two ways to get findings into a database:

- **Add to database** (in the bit field form) adds the one field you defined. If the database has no message for
  that identifier yet, it creates one (named `Message_500`, for example). The database editor then opens.
- **Promote to database** (in the automatic analysis) turns the *whole* analysis into a draft in one step: every
  non-constant field becomes a signal, with its kind, multiplexer condition and details kept in the signal's
  comment. Where two interpretations claim the same bits, the more confident one wins, so no signals overlap.

Promoting the whole analysis is a quick way to get a full skeleton. Adding fields one at a time gives you a cleaner
file. Most people do both: promote first, then go through the draft signal by signal.

### Cleaning up in the database editor

The **Database editor** (under *Tools*) is where a draft becomes a file other people can use.

![The database editor with BodyStatus (0x500) and its VehicleSpeed signal selected]({{ '/assets/img/blog/canexplorer/database-editor.png' | relative_url }})

`BodyStatus` (`0x500`) in the editor: the speed's position, byte order, scaling and unit, and a comment that records how it was found. `Message_0C0` hasn't been named yet: it's probably engine data, but that isn't confirmed.
{: .caption}

For each message:

- **Rename it** from `Message_500` to something meaningful, such as `BodyStatus`. Keep names unique and use only
  letters, digits and `_`, so the file converts cleanly to other formats.
- **Set the sender** node if you know which module sends it.

For each signal:

- Give it a **name**, a **unit**, a **factor**, an **offset** and a **minimum/maximum**.
- Add **value descriptions** for states and flags, such as `0=Closed; 1=Open` or `0=P; 1=R; 2=N; 3=D`. The trace then
  shows `DoorFrontLeft=Open` instead of `1`.
- Use the **comment** to record *how* you found and verified the signal: "notch, driver door, 5/5 markers" or
  "matched to OBD-II vehicle speed, r = 0.998". Six months from now this will matter more than anything else in the
  file.
- Mark **multiplexer** and **multiplexed** signals with their switch values.
- **Delete** what turned out to be noise.

The layout view shows each message's bits in colour per signal, and marks bits claimed by two signals in red.
*Problems* lists overlaps, signals running past the end of the frame, and duplicate identifiers.

![The bit layout of BodyStatus: door bit, speed, counter and checksum]({{ '/assets/img/blog/canexplorer/database-editor-layout.png' | relative_url }})

The layout of `BodyStatus`: the door bit (blue), the speed in bytes 2–3 (green), the alive counter in the low nibble of byte 6 and the CRC in byte 7. Each colour is one signal. No bit is claimed twice, so *Problems* is empty.
{: .caption}

**Undo** works (<kbd>Ctrl</kbd>+<kbd>Z</kbd>) and goes back as far as 200 steps, so experiment freely.

### Save, load, verify

**Save as…** writes the file. The format follows the extension, so pick `.dbc`. Then load it into your connection
with **Load database…**. From then on the trace, overview and plots show your signals by name:

![The trace decoding a second recording with found.dbc]({{ '/assets/img/blog/canexplorer/trace-decoded.png' | relative_url }})

A second recording decoded with `found.dbc`: `BodyStatus: DoorFrontLeft=Open, VehicleSpeed=40.67 km/h, …`. Identifier `0E0` stays raw because the database doesn't describe it yet.
{: .caption}

Finally, **verify on a recording you didn't build it from**. Replay a different drive and check that every signal
still makes sense. Signals that only looked right on the first capture are the ones that will mislead someone
later.

Here's what a small, honest result looks like as DBC text (an illustrative excerpt, not a real vehicle's file):

```text
BO_ 1280 BodyStatus: 8 BCM
 SG_ DoorFrontLeft : 8|1@1+ (1,0) [0|1] "" Vector__XXX
 SG_ VehicleSpeed : 23|16@0+ (0.01,0) [0|655.35] "km/h" Vector__XXX
 SG_ AliveCounter : 51|4@0+ (1,0) [0|15] "" Vector__XXX
 SG_ Checksum : 63|8@0+ (1,0) [0|255] "" Vector__XXX

CM_ SG_ 1280 DoorFrontLeft "Notch + markers: driver door, 5/5 hits.";
CM_ SG_ 1280 VehicleSpeed "Matched to OBD-II PID 0x0D, r=0.998, lag 0 ms. Verified on a second drive.";
CM_ SG_ 1280 Checksum "Detected as CRC-8; parameters not yet confirmed.";
VAL_ 1280 DoorFrontLeft 0 "Closed" 1 "Open" ;
```

Notice the last comment. If you're not sure about something, **say so in the file**. A database that admits
"not yet confirmed" is worth far more than one that guesses with confidence.

### Other formats

The same database can be saved as BUSMASTER DBF, Kayak KCD, PCAN SYM or AUTOSAR ARXML. Use **Save as…** with
another extension, **⋮ → Convert Signal Database…**, or the command line:

```bash
can-db-convert body.dbc body.kcd
can-db-convert body.dbc body.arxml
```

The conversion summary lists anything the target format can't hold.

## Optional: an AI assistant as a lab partner

CANExplorer ships `can-mcp`, a [Model Context Protocol](https://modelcontextprotocol.io) server that gives an AI
agent the same reverse-engineering tools: list identifiers, analyse them, correlate fields, and write confirmed
fields to a DBC. By default it runs read-only and sends nothing. For example, to connect Claude Code to a live bus:

```bash
claude mcp add canexplorer -- can-mcp -i socketcan -c can0
```

A request like *"Find the brake pedal signal and add it to found.dbc"* then runs the analysis and calls
`promote_to_dbc`, which extends the file rather than overwriting it. Treat the agent's findings like the automatic
analysis: a good first pass, which you verify with a plot.

## Sharing your database on dbchub

You've done the hard part. If you publish the file, the next owner of the same car can start from your work.
[dbchub](https://dbchub.com) is a Git-native catalog of signal databases: every file is versioned, validated by
CI, and reviewed before it's merged.

> **IMPORTANT** Upload only what you have the right to share. A database **you reverse engineered yourself** from
> your own vehicle is your work, and dbchub publishes it under CC BY 4.0. A DBC file leaked from a manufacturer or
> supplier, or copied from a dealer tool or a paid product, is not yours to publish, and dbchub will remove it.
> See [ACCEPTABLE_USE.md](https://github.com/AKJ7/dbchub/blob/master/ACCEPTABLE_USE.md).
{: .callout .warning}

1. **Name and place it.** Files live at `databases/<manufacturer>/<model>/<bus>/<name>.<ext>`, with lowercase
   folder names, for example `databases/volkswagen/golf-mk7/can/body.dbc`. Use the bus folder that matches what you
   recorded (`can`, `canfd`, `lin`…).
2. **Describe it (optional but appreciated).** A `model.yml` next to it can give the model's display name, years,
   aliases and the bus bitrate:

   ```yaml
   name: "Golf Mk7"
   years: "2013–2020"
   buses:
     can:
       name: "Comfort CAN (behind gateway)"
       bitrate: 500000
   ```

3. **Validate it locally** in a checkout of dbchub:

   ```bash
   uv run dbchub-validate path/to/body.dbc
   ```

   CI runs the same checks: does it parse, are there duplicate frame IDs, signals overflowing their frame, signals
   without units, and does cantools agree.
4. **Open a pull request**, either from the **Upload** button on the website or from the command line:

   ```bash
   export GITHUB_TOKEN=github_pat_...   # fine-grained, your fork only
   uv run dbchub upload body.dbc \
       --repo you/dbchub --upstream AKJ7/dbchub \
       --manufacturer volkswagen --model golf-mk7 --bus can
   ```

   Or use plain git: fork, branch, add the file, and sign off your commit with `git commit -s`.

In the PR description, say which vehicle and model year you recorded, where you tapped the bus, and how sure you are
about each part. Reviewers and later users will rely on that.

Before you start on a car, check whether it's already in the catalog. [Signal search]({{ '/search/' | relative_url }})
finds a signal or an identifier across every database, and improving an existing file is just as welcome as adding
a new one.

## Trying it without a vehicle

Want to practise before crawling under a dashboard? CANExplorer can make realistic traffic from any database, with
values that ramp, step, toggle and drift:

```bash
uv run python tools/synthetic_traffic.py examples/simulation/vehicle.dbc traffic.asc --duration 30 \
    --behaviour EngineData.GearPosition=step:10
```

Replay `traffic.asc` at normal speed, open **Reverse engineering**, select identifier `100`, press **Notch** before
the 10-second mark, and watch bits 24–27 when the gear changes. Then run the automatic analysis and compare what it
finds with the original `vehicle.dbc`. Because you know the right answer, it's good practice. You can do the same
with any database from dbchub.

## Summary

The workflow, in one screen:

1. **Connect** in listen-only mode at the right bitrate, and confirm Bus health shows no errors.
2. **Record** a scripted session (baseline, then one action at a time) and write down what you did and when.
3. **Get oriented** in the Overview: cycle times, event messages, counters.
4. **Find signals**: notch and markers for switches, reaction capture for "what did that do?", reference signals
   for physical values, automatic analysis for counters, checksums and multiplexers.
5. **Check every field by plotting it.**
6. **Build the database**: promote or add, then name, scale, describe and comment in the editor.
7. **Verify** on a different recording.
8. **Share** it on dbchub, and be honest about what's still uncertain.

Every signal you decode and publish saves the next person an afternoon. Happy hacking, and stay safe.

*Further reading: the CANExplorer documentation on
[reverse engineering](https://akj7.github.io/CANExplorer/user-guide/reverse-engineering/),
[the database editor](https://akj7.github.io/CANExplorer/user-guide/database-editor/) and
[supported interfaces](https://akj7.github.io/CANExplorer/reference/interfaces/).*
