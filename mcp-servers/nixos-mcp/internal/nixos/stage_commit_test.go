package nixos

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	gossh "golang.org/x/crypto/ssh"
)

const (
	bootGen      = 5
	stagedGen    = 4
	fakeHostName = "fake-host"
	fakeUser     = "fake-user"
	fakeDialWait = 5 * time.Second
	hookExitCode = 100
	unknownExit  = 255
	dirMode      = 0o755
	fileMode     = 0o644
)

const sudoScript = `#!/bin/sh
a=$1; shift
case "$a" in
  /nix/var/nix/profiles/*) a="$FAKE_ROOT/profiles/${a#/nix/var/nix/profiles/}";;
  /run/*) a="$FAKE_ROOT/run/${a#/run/}";;
esac
echo "sudo $a $*" | sed "s#$FAKE_ROOT##" >> "$FAKE_ROOT/calls"
exec "$a" "$@"
`

const systemctlScript = `#!/bin/sh
case "$1 $2" in
  "start rollback-gate.timer") touch "$FAKE_ROOT/timer";;
  "stop rollback-gate.timer") [ -e "$FAKE_ROOT/fail/timer-stop" ] && exit 1; rm -f "$FAKE_ROOT/timer";;
  "is-active rollback-gate.timer") [ -e "$FAKE_ROOT/timer" ] || exit 3;;
  "restart systemd-sysctl") ;;
  *) exit 1;;
esac
exit 0
`

const nixEnvScript = `#!/bin/sh
map() {
  case "$1" in
    /nix/var/nix/profiles/*) echo "$FAKE_ROOT/profiles/${1#/nix/var/nix/profiles/}";;
    /run/*) echo "$FAKE_ROOT/run/${1#/run/}";;
    *) echo "$1";;
  esac
}
case "$1" in
  --switch-generation)
    [ -e "$FAKE_ROOT/profiles/system-$2-link" ] || { echo "error: generation $2 does not exist" >&2; exit 1; }
    ln -sfn "system-$2-link" "$FAKE_ROOT/profiles/system";;
  -p)
    [ "$3" = "--set" ] || exit 1
    profile=$(map "$2")
    target=$(readlink -f "$(map "$4")")
    dir=$(dirname "$profile")
    last=$(ls "$dir" | sed -n 's/^system-\([0-9]*\)-link$/\1/p' | sort -n | tail -1)
    next=$((last + 1))
    ln -s "$target" "$dir/system-$next-link"
    ln -sfn "system-$next-link" "$profile";;
  *) exit 1;;
esac
`

const genScript = `#!/bin/sh
case "$1" in
  test)
    [ -e "$FAKE_ROOT/fail/test-before-activate" ] || ln -sfn "$FAKE_ROOT/store/%[1]d" "$FAKE_ROOT/run/current-system"
    exit $(cat "$FAKE_ROOT/test-exit" 2>/dev/null || echo 0);;
  boot)
    echo %[1]d > "$FAKE_ROOT/boot_default";;
  *) exit 1;;
esac
`

type fakeHost struct {
	t      *testing.T
	root   string
	client *realNixOSClient
}

func write(t *testing.T, path, body string, mode os.FileMode) {
	t.Helper()
	if err := os.WriteFile(path, []byte(body), mode); err != nil {
		t.Fatal(err)
	}
}

func newFakeHost(t *testing.T) *fakeHost {
	t.Helper()
	root := t.TempDir()
	for _, d := range []string{"bin", "profiles", "fail", "run"} {
		if err := os.MkdirAll(filepath.Join(root, d), dirMode); err != nil {
			t.Fatal(err)
		}
	}
	write(t, filepath.Join(root, "bin/sudo"), sudoScript, dirMode)
	write(t, filepath.Join(root, "bin/systemctl"), systemctlScript, dirMode)
	write(t, filepath.Join(root, "bin/nix-env"), nixEnvScript, dirMode)
	for _, gen := range []int{stagedGen, bootGen} {
		storeDir := filepath.Join(root, "store", strconv.Itoa(gen))
		if err := os.MkdirAll(filepath.Join(storeDir, "bin"), dirMode); err != nil {
			t.Fatal(err)
		}
		write(t, filepath.Join(storeDir, "bin/switch-to-configuration"), fmt.Sprintf(genScript, gen), dirMode)
		link := filepath.Join(root, "profiles", fmt.Sprintf("system-%d-link", gen))
		if err := os.Symlink(storeDir, link); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink(fmt.Sprintf("system-%d-link", bootGen), filepath.Join(root, "profiles/system")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, "store", strconv.Itoa(bootGen)), filepath.Join(root, "run/current-system")); err != nil {
		t.Fatal(err)
	}
	for _, f := range []string{"booted", "boot_default"} {
		write(t, filepath.Join(root, f), fmt.Sprintf("%d\n", bootGen), fileMode)
	}
	write(t, filepath.Join(root, "calls"), "", fileMode)
	h := &fakeHost{t: t, root: root}
	h.client = h.startSSH()
	return h
}

func exitStatus(err error) uint32 {
	if err == nil {
		return 0
	}
	if ee, ok := err.(*exec.ExitError); ok {
		return uint32(ee.ExitCode())
	}
	return unknownExit
}

func (h *fakeHost) startSSH() *realNixOSClient {
	h.t.Helper()
	_, hostPriv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		h.t.Fatal(err)
	}
	hostSigner, err := gossh.NewSignerFromKey(hostPriv)
	if err != nil {
		h.t.Fatal(err)
	}
	cfg := &gossh.ServerConfig{NoClientAuth: true}
	cfg.AddHostKey(hostSigner)
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		h.t.Fatal(err)
	}
	h.t.Cleanup(func() { _ = lis.Close() })
	go func() {
		for {
			c, err := lis.Accept()
			if err != nil {
				return
			}
			go h.serve(c, cfg)
		}
	}()
	return &realNixOSClient{
		user:        fakeUser,
		hostAddrs:   map[string]string{fakeHostName: lis.Addr().String()},
		dialTimeout: fakeDialWait,
		dialRetries: 1,
		dialBackoff: time.Millisecond,
		dialFunc:    (&net.Dialer{}).DialContext,
		signer:      hostSigner,
	}
}

func (h *fakeHost) serve(c net.Conn, cfg *gossh.ServerConfig) {
	conn, chans, reqs, err := gossh.NewServerConn(c, cfg)
	if err != nil {
		return
	}
	defer func() { _ = conn.Close() }()
	go gossh.DiscardRequests(reqs)
	for nc := range chans {
		ch, requests, err := nc.Accept()
		if err != nil {
			return
		}
		for req := range requests {
			if req.Type != "exec" {
				_ = req.Reply(false, nil)
				continue
			}
			var p struct{ Cmd string }
			_ = gossh.Unmarshal(req.Payload, &p)
			_ = req.Reply(true, nil)
			cmd := exec.Command("sh", "-c", p.Cmd)
			cmd.Env = append(os.Environ(),
				"PATH="+filepath.Join(h.root, "bin")+string(os.PathListSeparator)+os.Getenv("PATH"),
				"FAKE_ROOT="+h.root)
			out, runErr := cmd.CombinedOutput()
			_, _ = ch.Write(out)
			_, _ = ch.SendRequest("exit-status", false, gossh.Marshal(struct{ S uint32 }{exitStatus(runErr)}))
			_ = ch.Close()
			break
		}
	}
}

func (h *fakeHost) read(name string) int {
	h.t.Helper()
	b, err := os.ReadFile(filepath.Join(h.root, name))
	if err != nil {
		h.t.Fatal(err)
	}
	n, err := strconv.Atoi(strings.TrimSpace(string(b)))
	if err != nil {
		h.t.Fatal(err)
	}
	return n
}

func (h *fakeHost) generationOf(link string) int {
	h.t.Helper()
	target, err := filepath.EvalSymlinks(filepath.Join(h.root, link))
	if err != nil {
		h.t.Fatal(err)
	}
	n, err := strconv.Atoi(filepath.Base(target))
	if err != nil {
		h.t.Fatal(err)
	}
	return n
}

func (h *fakeHost) profile() int { return h.generationOf("profiles/system") }

func (h *fakeHost) running() int { return h.generationOf("run/current-system") }

func (h *fakeHost) armed() bool {
	_, err := os.Stat(filepath.Join(h.root, "timer"))
	return err == nil
}

func (h *fakeHost) calls() string {
	h.t.Helper()
	b, err := os.ReadFile(filepath.Join(h.root, "calls"))
	if err != nil {
		h.t.Fatal(err)
	}
	return string(b)
}

func (h *fakeHost) fail(name string) {
	h.t.Helper()
	write(h.t, filepath.Join(h.root, "fail", name), "", fileMode)
}

func (h *fakeHost) failTest(exitCode int) {
	h.t.Helper()
	write(h.t, filepath.Join(h.root, "test-exit"), fmt.Sprintf("%d\n", exitCode), fileMode)
}

func (h *fakeHost) stage() error {
	_, err := h.client.StageGeneration(context.Background(), fakeHostName, stagedGen)
	return err
}

func (h *fakeHost) commit() error {
	_, err := h.client.CommitGeneration(context.Background(), fakeHostName)
	return err
}

func (h *fakeHost) deadManReboot() {
	h.t.Helper()
	boot := h.read("boot_default")
	if err := os.Remove(filepath.Join(h.root, "run/current-system")); err != nil {
		h.t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(h.root, "store", strconv.Itoa(boot)), filepath.Join(h.root, "run/current-system")); err != nil {
		h.t.Fatal(err)
	}
	write(h.t, filepath.Join(h.root, "booted"), fmt.Sprintf("%d\n", boot), fileMode)
	_ = os.Remove(filepath.Join(h.root, "timer"))
}

func TestCommitGenerationPromotesTheRunningSystem(t *testing.T) {
	h := newFakeHost(t)
	if err := h.stage(); err != nil {
		t.Fatal(err)
	}
	if err := h.commit(); err != nil {
		t.Fatalf("commit: %v", err)
	}
	if got := h.read("boot_default"); got != stagedGen {
		t.Errorf("boot default = %d, want %d", got, stagedGen)
	}
	if got := h.profile(); got != stagedGen {
		t.Errorf("profile = %d, want %d", got, stagedGen)
	}
	if got := h.running(); got != stagedGen {
		t.Errorf("running = %d, want %d", got, stagedGen)
	}
	if h.armed() {
		t.Error("timer still armed")
	}
}

func TestCommitGenerationAfterARevertRebootRefuses(t *testing.T) {
	h := newFakeHost(t)
	if err := h.stage(); err != nil {
		t.Fatal(err)
	}
	h.deadManReboot()
	if err := h.commit(); err == nil {
		t.Error("commit succeeded with the timer gone")
	}
	if got := h.read("boot_default"); got != bootGen {
		t.Errorf("boot default = %d, want %d", got, bootGen)
	}
	if got := h.running(); got != bootGen {
		t.Errorf("running = %d, want %d", got, bootGen)
	}
	if strings.Contains(h.calls(), "switch-to-configuration boot") {
		t.Errorf("boot default written: %s", h.calls())
	}
}

func TestCommitGenerationWithoutAStageRefuses(t *testing.T) {
	h := newFakeHost(t)
	if err := h.commit(); err == nil {
		t.Error("commit succeeded without a stage")
	}
	if got := h.read("boot_default"); got != bootGen {
		t.Errorf("boot default = %d, want %d", got, bootGen)
	}
}

func TestCommitGenerationRealignsTheProfileWithTheRunningSystem(t *testing.T) {
	h := newFakeHost(t)
	h.fail("test-before-activate")
	h.failTest(hookExitCode)
	if err := h.stage(); err == nil {
		t.Fatal("stage succeeded")
	}
	if got := h.running(); got != bootGen {
		t.Fatalf("running = %d, want %d", got, bootGen)
	}
	if got := h.profile(); got != stagedGen {
		t.Fatalf("profile = %d, want %d", got, stagedGen)
	}
	if err := h.commit(); err != nil {
		t.Fatalf("commit: %v", err)
	}
	for name, got := range map[string]int{"profile": h.profile(), "boot default": h.read("boot_default"), "running": h.running()} {
		if got != bootGen {
			t.Errorf("%s = %d, want %d", name, got, bootGen)
		}
	}
}

func TestCommitGenerationWithAFailedStopLeavesTheRunningSystemAsBootDefault(t *testing.T) {
	h := newFakeHost(t)
	if err := h.stage(); err != nil {
		t.Fatal(err)
	}
	h.fail("timer-stop")
	if err := h.commit(); err == nil {
		t.Fatal("commit succeeded with the timer stuck")
	}
	h.deadManReboot()
	if got := h.read("booted"); got != stagedGen {
		t.Errorf("booted = %d, want %d", got, stagedGen)
	}
}
