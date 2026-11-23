#!/usr/bin/env python3
"""qnxrun - one-command QNX 8.0 image builder and QEMU runner.

Builds a bootable QNX 8.0 image containing your binary with mkqnximage and
boots it headless in QEMU, streaming the serial console to your terminal.

It papers over the sharp edges of the stock flow:
  - mkqnximage's runimage requires brctl + a setuid qemu-bridge-helper +
    a bridge interface for networking. qnxrun launches QEMU directly with
    no NIC (or user networking), so no host network setup is needed.
  - the Software Center CLI stores the license under /root/.qnx while qcc
    reads $HOME/.qnx/license. `qnxrun doctor` spots this and `build`
    copies the license file into place.
  - the mkqnximage local/snippets dance (ifs_files.custom +
    post_start.custom) is generated for you.

Requires: QNX SDP 8.0 installed (qnxsdp-env.sh), mkqnximage, qcc,
qemu-system-x86_64 / qemu-system-aarch64. Linux host.

Usage:
  qnxrun doctor
  qnxrun new myapp [--dir DIR]
  qnxrun build --binary ./hello-x86_64 [--arch x86_64|aarch64le]
               [--start-cmd "/proc/boot/hello"] [--project-dir DIR]
  qnxrun run [--project-dir DIR] [--timeout 60] [--log FILE]
  qnxrun logs [--project-dir DIR]
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time

VERSION = "0.1.0"

DEFAULT_SDP_CANDIDATES = [
    os.environ.get("QNX_SDP"),
    os.path.expanduser("~/qnx/sdp-8.0"),
    "/opt/qnx/sdp-8.0",
    os.environ.get("QNX_TARGET", "").rsplit("/target", 1)[0] or None,
]


def find_sdp(explicit=None):
    """Return the SDP install dir, or None."""
    for cand in [explicit] + DEFAULT_SDP_CANDIDATES:
        if cand and os.path.isfile(os.path.join(cand, "qnxsdp-env.sh")):
            return cand
    return None


def sdp_prefix(sdp):
    """Shell prefix that sources the SDP environment."""
    return "source '%s' >/dev/null 2>&1; " % os.path.join(sdp, "qnxsdp-env.sh")


def sh(cmd, sdp=None, check=True, capture=False, cwd=None):
    """Run a shell command (with the SDP env sourced when sdp is given)."""
    full = (sdp_prefix(sdp) + cmd) if sdp else cmd
    res = subprocess.run(["bash", "-c", full], cwd=cwd,
                         capture_output=capture, text=True)
    if check and res.returncode != 0:
        raise RuntimeError("command failed (%d): %s\n%s" %
                           (res.returncode, cmd,
                            (res.stderr or "")[-2000:]))
    return res


def license_locations():
    """(qcc-visible dir, cli-visible dir) for the QNX license file."""
    home_lic = os.path.expanduser("~/.qnx/license/licenses")
    root_lic = "/root/.qnx/license/licenses"
    return home_lic, root_lic


def ensure_license(sdp=None, quiet=False):
    """Make sure qcc can see a license; copy it from the CLI dir if needed.

    Returns (ok, message).
    """
    home_lic, root_lic = license_locations()
    if os.path.isfile(home_lic):
        return True, "license found at %s" % home_lic
    if os.path.isfile(root_lic):
        os.makedirs(os.path.dirname(home_lic), exist_ok=True)
        shutil.copy2(root_lic, home_lic)
        return True, ("license copied from %s to %s "
                      "(Software Center CLI and qcc use different homes)"
                      % (root_lic, home_lic))
    return False, ("no license file found (looked in %s and %s); "
                   "activate one with qnxsoftwarecenter_clt "
                   "-addLicenseKey <key> -activateAll" % (home_lic, root_lic))


def cmd_doctor(args):
    sdp = find_sdp(args.sdp)
    rows = []

    def row(name, ok, detail=""):
        rows.append((name, ok, detail))

    row("QNX SDP 8.0", bool(sdp), sdp or "not found (use --sdp)")
    if sdp:
        qcc = shutil.which("qcc") or os.path.join(
            sdp, "host/linux/x86_64/usr/bin/qcc")
        row("qcc", os.path.isfile(qcc), qcc if os.path.isfile(qcc) else "")
        mkimg = os.path.join(sdp, "host/common/bin/mkqnximage")
        row("mkqnximage", os.path.isfile(mkimg), mkimg)
    for arch in ("x86_64", "aarch64"):
        qemu = shutil.which("qemu-system-%s" % arch)
        row("qemu-system-%s" % arch, bool(qemu), qemu or "missing (apt install qemu-system)")
    ok, msg = ensure_license()
    row("QNX license (qcc-visible)", ok, msg)
    proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    row("egress proxy (only needed for downloads)", bool(proxy),
        proxy.split("@")[-1] if proxy else "not set")

    bad = 0
    print("qnxrun doctor")
    for name, ok, detail in rows:
        print("  [%s] %s%s" % ("OK  " if ok else "FAIL", name,
                               (" - " + detail) if detail else ""))
        bad += 0 if ok else 1
    if bad:
        print("%d check(s) failed - fix the FAIL rows above and re-run." % bad)
        return 1
    print("All checks passed.")
    return 0


HELLO_C = """#include <stdio.h>
int main(void) {
    printf("Hello from QNX 8.0!\\n");
    return 0;
}
"""

MAKEFILE = """# Build with: source $QNX_SDP/qnxsdp-env.sh && make
SDP ?= $(HOME)/qnx/sdp-8.0
ARCH ?= x86_64
TARGET = hello-$(ARCH)
CC = qcc
CFLAGS = -Vgcc_nto$(ARCH)

all: $(TARGET)

$(TARGET): hello.c
\t$(CC) $(CFLAGS) -o $@ hello.c

clean:
\trm -f hello-x86_64 hello-aarch64le

.PHONY: all clean
"""


def cmd_new(args):
    d = args.dir
    if os.path.exists(d) and os.listdir(d):
        print("error: %s already exists and is not empty" % d, file=sys.stderr)
        return 1
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "hello.c"), "w") as f:
        f.write(HELLO_C)
    with open(os.path.join(d, "Makefile"), "w") as f:
        f.write(MAKEFILE)
    print("Scaffolded QNX hello project in %s" % d)
    print("  cd %s && source ~/qnx/sdp-8.0/qnxsdp-env.sh && make" % d)
    print("  qnxrun build --binary ./hello-x86_64")
    return 0


def cmd_build(args):
    sdp = find_sdp(args.sdp)
    if not sdp:
        print("error: QNX SDP 8.0 not found (use --sdp)", file=sys.stderr)
        return 1
    binary = os.path.abspath(args.binary)
    if not os.path.isfile(binary):
        print("error: binary not found: %s" % binary, file=sys.stderr)
        return 1
    arch = args.arch
    proj = os.path.abspath(args.project_dir)
    name = os.path.basename(binary)
    start_cmd = args.start_cmd or ("/proc/boot/%s" % name)

    ok, msg = ensure_license()
    print("license: %s" % msg)
    if not ok:
        return 1

    snip = os.path.join(proj, "local", "snippets")
    os.makedirs(snip, exist_ok=True)
    with open(os.path.join(snip, "ifs_files.custom"), "w") as f:
        f.write("%s=%s\n" % (name, binary))
    with open(os.path.join(snip, "post_start.custom"), "w") as f:
        f.write("%s\n" % start_cmd)
    print("project dir: %s" % proj)
    print("  IFS entry: %s=%s" % (name, binary))
    print("  boot command: %s" % start_cmd)

    print("building image (mkqnximage, this takes ~30s)...")
    sh("mkqnximage --type=qemu --arch=%s --build --noprompt --force" % arch,
       sdp=sdp, check=False, cwd=proj)
    ifs = os.path.join(proj, "output", "ifs.bin")
    disk = os.path.join(proj, "output", "disk-qemu")
    if not (os.path.isfile(ifs) and os.path.isfile(disk)):
        print("error: image build failed - no ifs.bin/disk found under %s"
              % os.path.join(proj, "output"), file=sys.stderr)
        return 1
    print("image built:")
    print("  %s (%d MB)" % (ifs, os.path.getsize(ifs) // 1024 // 1024))
    print("  %s (%d MB)" % (disk, os.path.getsize(disk) // 1024 // 1024))
    print("run it with: qnxrun run --project-dir %s" % proj)
    return 0


def qemu_cmd(proj, arch, log_path):
    """Direct QEMU launch (no bridge networking needed)."""
    out = os.path.join(proj, "output")
    base = ["-smp", "2", "--cpu", "max", "-m", "1024",
            "-pidfile", os.path.join(out, "qemu.pid"),
            "-nographic",
            "-kernel", os.path.join(out, "ifs.bin"),
            "-serial", "file:%s" % log_path,
            "-object", "rng-random,filename=/dev/urandom,id=rng0",
            "-device", "virtio-rng-pci,rng=rng0"]
    if arch == "x86_64":
        return (["qemu-system-x86_64"] + base[:2] + base[2:4] +
                ["-drive", "file=%s,if=ide,id=drv0" %
                 os.path.join(out, "disk-qemu.vmdk")] + base[4:])
    return (["qemu-system-aarch64", "-machine", "virt-4.2",
             "-cpu", "cortex-a57"] + base[:2] + base[2:4] +
            ["-drive", "file=%s,if=none,id=drv0" %
             os.path.join(out, "disk-qemu.vmdk"),
             "-device", "virtio-blk-device,drive=drv0"] + base[4:])


def stream_log(path, proc, timeout):
    """Stream a growing serial log to stdout until timeout/proc exit."""
    start = time.time()
    pos = 0
    try:
        while True:
            if timeout and (time.time() - start) > timeout:
                print("\n-- timeout (%ds), stopping QEMU --" % timeout)
                break
            if proc.poll() is not None:
                break
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                if chunk:
                    pos += len(chunk)
                    sys.stdout.write(chunk.decode("utf-8", "replace"))
                    sys.stdout.flush()
            time.sleep(0.2)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    # drain whatever is left
    if os.path.isfile(path):
        with open(path, "rb") as f:
            f.seek(pos)
            tail = f.read()
        if tail:
            sys.stdout.write(tail.decode("utf-8", "replace"))
            sys.stdout.flush()


def cmd_run(args):
    proj = os.path.abspath(args.project_dir)
    out = os.path.join(proj, "output")
    ifs = os.path.join(out, "ifs.bin")
    if not os.path.isfile(ifs):
        print("error: no image in %s - run `qnxrun build` first" % proj,
              file=sys.stderr)
        return 1
    arch = args.arch
    log_path = (os.path.abspath(args.log) if args.log
                else os.path.join(out, "qemu.out"))
    qemu = shutil.which("qemu-system-%s" %
                        ("x86_64" if arch == "x86_64" else "aarch64"))
    if not qemu:
        print("error: qemu-system-%s not installed" % arch, file=sys.stderr)
        return 1
    if os.path.exists(log_path):
        os.remove(log_path)
    cmd = qemu_cmd(proj, arch, log_path)
    print("QEMU: %s" % " ".join(cmd))
    print("serial log: %s (Ctrl-C to stop)" % log_path)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        stream_log(log_path, proc, args.timeout)
    except KeyboardInterrupt:
        print("\n-- interrupted, stopping QEMU --")
        if proc.poll() is None:
            proc.terminate()
    print("QEMU exited (rc=%s)" % proc.poll())
    return 0


def cmd_logs(args):
    proj = os.path.abspath(args.project_dir)
    path = os.path.join(proj, "output", "qemu.out")
    if not os.path.isfile(path):
        print("no serial log yet at %s" % path, file=sys.stderr)
        return 1
    with open(path, "rb") as f:
        sys.stdout.write(f.read().decode("utf-8", "replace"))
    return 0


def main():
    p = argparse.ArgumentParser(
        prog="qnxrun",
        description="One-command QNX 8.0 image builder and QEMU runner.")
    p.add_argument("--version", action="version", version="qnxrun " + VERSION)
    p.add_argument("--sdp", default=None,
                   help="QNX SDP install dir (default: ~/qnx/sdp-8.0)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check SDP, qcc, QEMU, license")

    pn = sub.add_parser("new", help="scaffold a hello-qnx project")
    pn.add_argument("--dir", default="hello-qnx")

    pb = sub.add_parser("build", help="build a bootable QEMU image")
    pb.add_argument("--binary", required=True,
                    help="QNX binary to put in the image")
    pb.add_argument("--arch", default="x86_64",
                    choices=["x86_64", "aarch64le"])
    pb.add_argument("--start-cmd", default=None,
                    help="command run at boot (default: /proc/boot/<binary>)")
    pb.add_argument("--project-dir", default="./qnx-image")

    pr = sub.add_parser("run", help="boot the image in QEMU, stream serial")
    pr.add_argument("--project-dir", default="./qnx-image")
    pr.add_argument("--arch", default="x86_64",
                    choices=["x86_64", "aarch64le"])
    pr.add_argument("--timeout", type=int, default=0,
                    help="seconds to run (0 = until Ctrl-C)")
    pr.add_argument("--log", default=None, help="serial log file")

    pl = sub.add_parser("logs", help="show the last serial log")
    pl.add_argument("--project-dir", default="./qnx-image")

    args = p.parse_args()
    fn = {"doctor": cmd_doctor, "new": cmd_new, "build": cmd_build,
          "run": cmd_run, "logs": cmd_logs}[args.cmd]
    try:
        return fn(args)
    except RuntimeError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
