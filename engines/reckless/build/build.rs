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

fn generate_model_env() {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let mut path = match env::var("EVALFILE") {
        Ok(value) => PathBuf::from(value),
        Err(_) => {
            let fetch = manifest_dir.join("../../scripts/fetch-reckless-network.sh");
            if !fetch.is_file() {
                panic!(
                    "EVALFILE is unset and the Allfather verified NNUE fetch helper is unavailable: {}",
                    fetch.display()
                );
            }
            let output = Command::new(&fetch)
                .output()
                .expect("failed to execute the Allfather verified NNUE fetch helper");
            if !output.status.success() {
                panic!(
                    "Allfather verified NNUE fetch helper failed: {}",
                    String::from_utf8_lossy(&output.stderr)
                );
            }
            let value = String::from_utf8(output.stdout)
                .expect("verified NNUE helper returned a non-UTF-8 path");
            PathBuf::from(value.trim())
        }
    };

    if path.is_relative() {
        path = manifest_dir.join(path);
    }

    if !path.is_file() {
        panic!("verified EVALFILE does not point to a readable NNUE file: {}", path.display());
    }

    println!("cargo:rustc-env=MODEL={}", path.display());
    println!("cargo:rerun-if-changed=../../scripts/fetch-reckless-network.sh");
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
