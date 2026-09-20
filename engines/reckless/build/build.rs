use std::{
    env,
    fs::File,
    io::{BufWriter, Write},
    path::{Path, PathBuf},
    process::Command,
};

mod attacks;
mod magics;
mod maps;

fn main() {
    generate_model_env();
    generate_attack_maps();
    generate_compiler_info();
    generate_engine_version();

    #[cfg(feature = "syzygy")]
    if std::env::var("CARGO_CFG_TARGET_ARCH").as_deref() != Ok("wasm32") {
        generate_syzygy_binding();
    }

    println!("cargo:rerun-if-env-changed=EVALFILE");
    println!("cargo:rerun-if-changed=.git/HEAD");
    println!("cargo:rerun-if-changed=.git/logs/HEAD");
}

#[cfg(feature = "syzygy")]
fn generate_syzygy_binding() {
    cc::Build::new()
        .compiler("clang")
        .include("./deps/Fathom")
        .file("./deps/Fathom/tbprobe.c")
        .flag("-Wno-deprecated-declarations")
        .flag("-Wno-sign-compare")
        .flag("-Wno-macro-redefined")
        .flag("-O3")
        .compile("fathom");

    bindgen::Builder::default()
        .header("./deps/Fathom/tbprobe.h")
        .layout_tests(false)
        .generate()
        .expect("Failed to generate Fathom bindings")
        .write_to_file("src/bindings.rs")
        .unwrap();
}

fn run_verified_nnue_fetch(fetch: &Path) -> PathBuf {
    let mut candidates: Vec<(String, Vec<String>)> = Vec::new();

    if let Ok(value) = env::var("PYTHON") {
        if !value.trim().is_empty() {
            candidates.push((value, Vec::new()));
        }
    }

    if cfg!(windows) {
        candidates.push(("py".to_string(), vec!["-3".to_string()]));
        candidates.push(("python".to_string(), Vec::new()));
        candidates.push(("python3".to_string(), Vec::new()));
    } else {
        candidates.push(("python3".to_string(), Vec::new()));
        candidates.push(("python".to_string(), Vec::new()));
    }

    for (program, args) in candidates {
        let mut command = Command::new(&program);
        command.args(&args).arg(fetch);

        match command.output() {
            Ok(output) => {
                if !output.status.success() {
                    panic!(
                        "Allfather verified NNUE fetch helper failed via {}: {}",
                        program,
                        String::from_utf8_lossy(&output.stderr)
                    );
                }
                let value = String::from_utf8(output.stdout)
                    .expect("verified NNUE helper returned a non-UTF-8 path");
                let trimmed = value.trim();
                if trimmed.is_empty() {
                    panic!("verified NNUE helper returned an empty path");
                }
                return PathBuf::from(trimmed);
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => {
                panic!(
                    "failed to launch Python interpreter {} for verified NNUE fetch: {}",
                    program, error
                );
            }
        }
    }

    panic!(
        "EVALFILE is unset and no Python 3 interpreter was found; set PYTHON to a Python 3 executable or provide EVALFILE explicitly"
    );
}

fn generate_model_env() {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let (mut path, source_label) = match env::var("EVALFILE") {
        Ok(value) => (PathBuf::from(value), "explicit EVALFILE override"),
        Err(_) => {
            let fetch = manifest_dir.join("../../scripts/fetch-reckless-network.py");
            if !fetch.is_file() {
                panic!(
                    "EVALFILE is unset and the portable Allfather verified NNUE fetch helper is unavailable: {}",
                    fetch.display()
                );
            }
            (run_verified_nnue_fetch(&fetch), "pinned Allfather NNUE")
        }
    };

    if path.is_relative() {
        path = manifest_dir.join(path);
    }

    if !path.is_file() {
        panic!(
            "{} does not point to a readable NNUE file: {}",
            source_label,
            path.display()
        );
    }

    // The model bytes are embedded into the binary. Track the resolved file
    // itself so same-path replacement/corruption forces build.rs to rerun.
    println!("cargo:rerun-if-changed={}", path.display());
    println!("cargo:rustc-env=MODEL={}", path.display());
    println!("cargo:rerun-if-env-changed=PYTHON");
    println!("cargo:rerun-if-env-changed=ALLFATHER_ARTIFACT_DIR");
    println!("cargo:rerun-if-changed=../../scripts/fetch-reckless-network.py");
    println!("cargo:rerun-if-changed=../../vendor.lock.json");
}

fn generate_attack_maps() {
    let dir = env::var("OUT_DIR").unwrap();
    let path = Path::new(&dir).join("lookup.rs");
    let out = File::create(path).unwrap();
    write(BufWriter::new(out)).unwrap();
}

fn write(mut buf: BufWriter<File>) -> Result<(), std::io::Error> {
    macro_rules! write_map {
        ($name:tt, $type:tt, $items:expr) => {
            writeln!(buf, "static {}: [{}; {}] = {:?};", $name, $type, $items.len(), $items)?;
        };
    }

    write_map!("DIAGONALS", "[u64; 64]", maps::generate_diagonal_tables());

    write_map!("KING_MAP", "u64", maps::generate_king_map());
    write_map!("KNIGHT_MAP", "u64", maps::generate_knight_map());

    write_map!("PAWN_MAP", "[u64; 64]", maps::generate_pawn_map());

    write_map!("RAYPASS", "[u64; 64]", maps::generate_rays_map());
    write_map!("BETWEEN", "[u64; 64]", maps::generate_between_map());

    write_map!("ROOK_MAP", "u64", maps::generate_rook_map());
    write_map!("BISHOP_MAP", "u64", maps::generate_bishop_map());

    write_map!("ROOK_MAGICS", "MagicEntry", magics::ROOK_MAGICS);
    write_map!("BISHOP_MAGICS", "MagicEntry", magics::BISHOP_MAGICS);

    writeln!(buf, "struct MagicEntry {{ pub mask: u64, pub magic: u64, pub shift: u32, pub offset: u32 }}")
}


fn generate_compiler_info() {
    fn get_env(key: &str) -> String {
        env::var(key).unwrap_or("unknown".to_owned())
    }

    let version = Command::new("rustc")
        .arg("--version")
        .output()
        .map(|v| String::from_utf8_lossy(&v.stdout).to_string())
        .unwrap_or("unknown".to_owned());

    println!("cargo:rustc-env=COMPILER_VERSION={version}");
    println!("cargo:rustc-env=COMPILER_TARGET={}", get_env("TARGET"));
    println!("cargo:rustc-env=COMPILER_FEATURES={}", get_env("CARGO_CFG_TARGET_FEATURE"));
}

fn generate_engine_version() {
    let version = env!("CARGO_PKG_VERSION");

    let git_sha = Command::new("git")
        .args(["rev-parse", "--short=8", "HEAD"])
        .output()
        .ok()
        .filter(|v| v.status.success())
        .and_then(|v| String::from_utf8(v.stdout).ok())
        .map(|v| v.trim().to_string());

    if let Some(sha) = git_sha {
        println!("cargo:rustc-env=ENGINE_VERSION={version}-{sha}")
    } else {
        println!("cargo:rustc-env=ENGINE_VERSION={version}")
    }
}
