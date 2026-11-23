# qnxrun

One-command QNX 8.0 image builder and QEMU runner.

```sh
qnxrun doctor                                  # check SDP, qcc, QEMU, license
qnxrun new myapp                               # scaffold a hello-qnx project
qnxrun build --binary ./hello-x86_64           # build a bootable QEMU image
qnxrun run --project-dir ./qnx-image --timeout 60   # boot it, stream the serial console
```

Built for the **QNX Global Hackathon 2026** (Developer Tooling & Host Tools).

## The problem it solves

The stock `mkqnximage` flow works, but it has sharp edges that cost
real time the first time you meet them:

1. **`runimage` needs host networking setup.** It requires `brctl`,
   a setuid `qemu-bridge-helper`, and a bridge interface (`virbr0` /
   `br0`) before it will launch QEMU at all. qnxrun launches QEMU
   directly with no NIC, so no host network setup is needed — the
   guest doesn't need a network for most demos.
2. **The license lives in two places.** The Software Center CLI stores
   the license under `/root/.qnx/license`, while `qcc` reads
   `$HOME/.qnx/license`. `qnxrun doctor` spots the mismatch and
   `build` copies the license file into place.
3. **Image customisation is a snippet dance.** Adding your binary to
   the image means writing `local/snippets/ifs_files.custom` (IFS
   entry) and `local/snippets/post_start.custom` (boot command) by hand.
   `qnxrun build` generates both from `--binary` / `--start-cmd`.
4. **`mkqnximage` builds in the current directory**, and refuses
   non-empty dirs without `--force`. qnxrun handles both.

## Requirements

- QNX SDP 8.0 installed (default `~/qnx/sdp-8.0`, or `--sdp PATH`)
- `qemu-system-x86_64` / `qemu-system-aarch64` (`apt install qemu-system-x86 qemu-system-arm`)
- Python 3.8+ (standard library only, no dependencies)

## Usage

```
qnxrun doctor
qnxrun new myapp [--dir DIR]
qnxrun build --binary ./hello-x86_64 [--arch x86_64|aarch64le]
             [--start-cmd "/proc/boot/hello"] [--project-dir DIR]
qnxrun run [--project-dir DIR] [--timeout 60] [--log FILE]
qnxrun logs [--project-dir DIR]
```

`build` puts your binary into the IFS and starts it at boot
(`--start-cmd`, default `/proc/boot/<binary>`). `run` boots the image
headless, streams the serial console to your terminal, and stops QEMU
on `--timeout` or Ctrl-C. The serial log is saved under
`<project-dir>/output/qemu.out` (or `--log FILE`).

## Verified on

QNX SDP 8.0 (8.0.0.00141), QEMU 8.2.2, Ubuntu host. A hello-world
binary boots to `Hello from QNX 8.0!` on the serial console, and the
[QNX real-time patient monitor](https://github.com/sawi57003-ops/qnx-vitals-monitor)
runs its full 90-second demo through `qnxrun`.

## License

MIT — see [LICENSE](LICENSE).
