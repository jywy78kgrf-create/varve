// sumdb_split runs the REAL `go` command against a Go checksum database that
// this program controls, and forks that database, to answer one question:
//
//	when the go.sum database lies, who can tell, and when?
//
// This is not about varve. It is this notebook's one hard-won lesson —
// e000010, sharpened by e000018 and e000020: *a witness needs a verdict for "I
// cannot see," and any design that lacks one will spend that state as "I see a
// crime," or worse, as silence* — pointed at the transparency log the world's
// Go builds actually depend on. sum.golang.org is the deliberate version of
// what this notebook has been reinventing badly: a real Merkle log with signed
// tree heads. So: what does the deliberate one do in the state that broke ours?
//
// Four runs against three stacks. All three are signed by the SAME key, so
// every signature verifies everywhere; they differ only in what they record.
//
//	HONEST   record 0 = filler (clean), record 1 = widget (clean)
//	FORK@0   record 0 = widget (BACKDOORED)   -- diverges at the very first record
//	FORK@1   record 0 = filler (clean), record 1 = widget (BACKDOORED)
//
//	A  fresh client            + HONEST   -> baseline; the client caches a tree head
//	B  client holding head@2   + FORK@0   -> the fork is under the client's head
//	C  wiped client            + FORK@1   -> a machine that never saw the honest log
//	D  client holding head@1   + FORK@1   -> the fork is BEYOND the client's head
//
// D is the realistic one. A client's cached head is from its last build; the
// real log is 61 million records long and grows constantly, so any record an
// attacker adds is necessarily beyond where most clients' heads reach.
//
// Everything runs on localhost. No network is used except to build this
// program (it needs golang.org/x/mod, which supplies the SERVER side; the
// client under test is the go binary's own vendored verifier).
//
// Run:  go run .            -trace to log every request the go command makes
//
//	-keep  to leave the scratch dirs behind
package main

import (
	"archive/zip"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"golang.org/x/mod/module"
	"golang.org/x/mod/sumdb"
	"golang.org/x/mod/sumdb/dirhash"
	"golang.org/x/mod/sumdb/note"
)

const (
	sumdbName = "localsum.test"
	widget    = "example.com/widget"
	filler    = "example.com/filler"
	vers      = "v1.0.0"
)

var (
	trace = flag.Bool("trace", false, "log every HTTP request the go command makes")
	keep  = flag.Bool("keep", false, "keep scratch directories")
)

func goModFor(p string) string { return "module " + p + "\n\ngo 1.21\n" }

// buildZip writes a module zip whose single source file contains body.
// Callers must give distinct dirs for two copies of one module@version: the
// zip filename is derived from the module path, so a shared dir silently
// overwrites one copy with the other and there is no fork left to detect.
func buildZip(dir, p, v, body string) (string, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	path := filepath.Join(dir, strings.ReplaceAll(p, "/", "_")+"@"+v+".zip")
	f, err := os.Create(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	zw := zip.NewWriter(f)
	add := func(name, content string) error {
		w, err := zw.Create(p + "@" + v + "/" + name)
		if err != nil {
			return err
		}
		_, err = io.WriteString(w, content)
		return err
	}
	if err := add("go.mod", goModFor(p)); err != nil {
		return "", err
	}
	if err := add(filepath.Base(p)+".go", body); err != nil {
		return "", err
	}
	return path, zw.Close()
}

// goSumLines computes exactly the two go.sum lines the go command compares
// against: the zip's dirhash and the go.mod's dirhash.
func goSumLines(p, v, zipPath string) ([]byte, error) {
	zh, err := dirhash.HashZip(zipPath, dirhash.DefaultHash)
	if err != nil {
		return nil, err
	}
	mh, err := dirhash.Hash1([]string{"go.mod"}, func(string) (io.ReadCloser, error) {
		return io.NopCloser(strings.NewReader(goModFor(p))), nil
	})
	if err != nil {
		return nil, err
	}
	return []byte(fmt.Sprintf("%s %s %s\n%s %s/go.mod %s\n", p, v, zh, p, v, mh)), nil
}

type rec struct{ path, zip string }

// stack is one (module proxy + checksum database) pair on one port.
type stack struct {
	label string
	url   string
}

// newStack builds a log containing recs IN ORDER — record order is what
// decides where two logs diverge, so it must not come from a map.
func newStack(label, signerKey string, recs []rec) (*stack, error) {
	byPath := map[string]string{}
	for _, r := range recs {
		byPath[r.path] = r.zip
	}
	ts := sumdb.NewTestServer(signerKey, func(p, v string) ([]byte, error) {
		z, ok := byPath[p]
		if !ok {
			return nil, fmt.Errorf("no such module %s@%s", p, v)
		}
		return goSumLines(p, v, z)
	})
	for _, r := range recs {
		if _, err := ts.Lookup(context.Background(), module.Version{Path: r.path, Version: vers}); err != nil {
			return nil, err
		}
	}

	sdb := sumdb.NewServer(ts)
	mux := http.NewServeMux()
	// The checksum database, mirrored under the proxy — how the go command
	// reaches it whenever GOPROXY is set.
	mux.HandleFunc("/sumdb/"+sumdbName+"/supported", func(http.ResponseWriter, *http.Request) {})
	mux.HandleFunc("/sumdb/"+sumdbName+"/", func(w http.ResponseWriter, r *http.Request) {
		r2 := r.Clone(r.Context())
		r2.URL.Path = strings.TrimPrefix(r.URL.Path, "/sumdb/"+sumdbName)
		sdb.ServeHTTP(w, r2)
	})
	// A minimal module proxy.
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		p := strings.TrimPrefix(r.URL.Path, "/")
		i := strings.Index(p, "/@v/")
		if i < 0 {
			http.NotFound(w, r)
			return
		}
		mod, rest := p[:i], p[i+len("/@v/"):]
		switch {
		case rest == "list":
			fmt.Fprintln(w, vers)
		case strings.HasSuffix(rest, ".info"):
			json.NewEncoder(w).Encode(map[string]string{
				"Version": strings.TrimSuffix(rest, ".info"), "Time": "2026-01-01T00:00:00Z"})
		case strings.HasSuffix(rest, ".mod"):
			io.WriteString(w, goModFor(mod))
		case strings.HasSuffix(rest, ".zip"):
			z, ok := byPath[mod]
			if !ok {
				http.NotFound(w, r)
				return
			}
			http.ServeFile(w, r, z)
		default:
			http.NotFound(w, r)
		}
	})

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if *trace {
			fmt.Printf("        [%s] %s\n", label, r.URL.Path)
		}
		mux.ServeHTTP(w, r)
	})
	go (&http.Server{Handler: h}).Serve(ln)
	return &stack{label: label, url: "http://" + ln.Addr().String()}, nil
}

// goRun invokes the real go binary with a scratch GOPATH pointed at one stack.
func goRun(cache, work string, st *stack, pub string, args ...string) (string, int) {
	cmd := exec.Command("go", args...)
	cmd.Dir = work
	cmd.Env = append(os.Environ(),
		"GOPATH="+cache,
		"GOMODCACHE="+filepath.Join(cache, "pkg", "mod"),
		"GOPROXY="+st.url,
		"GOSUMDB="+pub,
		"GOPRIVATE=", "GONOSUMDB=", "GONOSUMCHECK=", "GOFLAGS=",
		"GOTOOLCHAIN=local",
	)
	out, err := cmd.CombinedOutput()
	code := 0
	if ee, ok := err.(*exec.ExitError); ok {
		code = ee.ExitCode()
	} else if err != nil {
		code = -1
	}
	return string(out), code
}

// head reports the tree head this client remembers. cmd/go keeps it in
// $GOPATH/pkg/sumdb/<name>/latest — NOT in the module cache, so
// `go clean -modcache` does not touch it.
func head(cache string) string {
	b, err := os.ReadFile(filepath.Join(cache, "pkg", "sumdb", sumdbName, "latest"))
	if err != nil {
		return "none — this client has never seen a tree head"
	}
	f := strings.Split(strings.TrimSpace(string(b)), "\n")
	if len(f) >= 3 {
		return "size " + f[1] + ", root " + f[2]
	}
	return strings.TrimSpace(string(b))
}

// installed reports what actually landed on disk.
func installed(cache string) string {
	b, err := os.ReadFile(filepath.Join(cache, "pkg", "mod", "example.com", "widget@"+vers, "widget.go"))
	if err != nil {
		return "no widget"
	}
	if strings.Contains(string(b), "exfiltrate") {
		return "*** BACKDOORED widget ***"
	}
	return "clean widget"
}

var headBefore string

func report(name, note, out string, code int, cache string) {
	fmt.Printf("\n%s\n   %s\n", name, note)
	fmt.Printf("   head before     : %s\n", headBefore)
	fmt.Printf("   go exit status  : %d\n", code)
	if s := strings.TrimRight(out, "\n"); s != "" {
		fmt.Printf("   go said         : %s\n", strings.ReplaceAll(s, "\n", "\n                     "))
	} else {
		fmt.Printf("   go said         : (nothing)\n")
	}
	fmt.Printf("   head after      : %s\n", head(cache))
	fmt.Printf("   on disk         : %s\n", installed(cache))
}

func main() {
	flag.Parse()
	tmp, err := os.MkdirTemp("", "sumdbsplit")
	check(err)
	if !*keep {
		defer os.RemoveAll(tmp)
	} else {
		fmt.Println("scratch:", tmp)
	}

	skey, vkey, err := note.GenerateKey(nil, sumdbName)
	check(err)

	cleanWidget, err := buildZip(filepath.Join(tmp, "clean"), widget, vers,
		"package widget\n\nfunc Greet() string { return \"hello\" }\n")
	check(err)
	evilWidget, err := buildZip(filepath.Join(tmp, "forked"), widget, vers,
		"package widget\n\nfunc Greet() string { return \"hello\" } // exfiltrate()\n")
	check(err)
	cleanFiller, err := buildZip(filepath.Join(tmp, "clean"), filler, vers, "package filler\n")
	check(err)

	honest, err := newStack("HONEST", skey, []rec{{filler, cleanFiller}, {widget, cleanWidget}})
	check(err)
	fork0, err := newStack("FORK@0", skey, []rec{{widget, evilWidget}, {filler, cleanFiller}})
	check(err)
	fork1, err := newStack("FORK@1", skey, []rec{{filler, cleanFiller}, {widget, evilWidget}})
	check(err)
	// An honest log caught EARLIER in its life: one record long, and that
	// record is byte-identical to FORK@1's record 0. Seeding a client here
	// gives it a head@1 — a head that predates the divergence.
	honest1, err := newStack("HONEST@1", skey, []rec{{filler, cleanFiller}})
	check(err)

	work := filepath.Join(tmp, "work")
	check(os.MkdirAll(work, 0o755))
	check(os.WriteFile(filepath.Join(work, "go.mod"), []byte("module scratch\n\ngo 1.21\n"), 0o644))

	fmt.Printf("checksum database %q, key generated for this run.\n", sumdbName)
	fmt.Printf("all three stacks are signed by that ONE key, so every signature verifies everywhere;\n")
	fmt.Printf("they differ only in what they record.\n")
	fmt.Printf("  HONEST  %s   record0=filler(clean)  record1=widget(clean)\n", honest.url)
	fmt.Printf("  FORK@0  %s   record0=widget(BACKDOORED)\n", fork0.url)
	fmt.Printf("  FORK@1  %s   record0=filler(clean)  record1=widget(BACKDOORED)\n", fork1.url)
	fmt.Printf("  HONEST@1 %s  record0=filler(clean)   — the honest log one record earlier\n", honest1.url)

	dl := func(cache string, st *stack, mod string) (string, int) {
		headBefore = head(cache)
		return goRun(cache, work, st, vkey, "mod", "download", mod+"@"+vers)
	}
	// wipeModules is `go clean -modcache`: it removes every module but leaves
	// $GOPATH/pkg/sumdb alone, so the client keeps its memory of the log.
	wipeModules := func(cache string) { os.RemoveAll(filepath.Join(cache, "pkg", "mod")) }

	cacheA := filepath.Join(tmp, "cacheA")
	out, code := dl(cacheA, honest, widget)
	report("A  fresh client, HONEST log",
		"baseline: nothing is wrong, and nothing should be reported.", out, code, cacheA)

	wipeModules(cacheA)
	out, code = dl(cacheA, fork0, widget)
	report("B  client holding head@2, FORK@0",
		"the fork is at record 0 — underneath the head this client already holds.", out, code, cacheA)

	cacheC := filepath.Join(tmp, "cacheC")
	out, code = dl(cacheC, fork1, widget)
	report("C  wiped client, FORK@1",
		"a machine that has never seen the honest log: a CI runner, a fresh container.", out, code, cacheC)

	cacheD := filepath.Join(tmp, "cacheD")
	if out, code = dl(cacheD, honest1, filler); code != 0 { // seed a head covering record 0 only
		fmt.Println("seeding D failed:", out)
		os.Exit(1)
	}
	wipeModules(cacheD)
	out, code = dl(cacheD, fork1, widget)
	report("D  client holding head@1, FORK@1",
		"the fork is at record 1 — BEYOND the head this client holds. The realistic case.", out, code, cacheD)
}

func check(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "fatal:", err)
		os.Exit(1)
	}
}
