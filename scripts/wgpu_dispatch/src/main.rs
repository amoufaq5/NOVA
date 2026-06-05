//! GPU-compute dispatcher for NOVA's vector-add smoke test.
//!
//! Invoked as `wgpu_dispatch <shader.wgsl> <sizes.cfg>` from `make
//! smoke-gpu`. Reads the WGSL shader and a tiny `key=value` config
//! file produced by `examples/gpu_vector_add.nova`, then:
//!
//!   1. Picks a wgpu adapter (Vulkan / Metal / D3D12 / GL / OpenGL ES,
//!      whichever the runtime exposes; falls back to the LavaPipe
//!      software backend if no real GPU is visible).
//!   2. Allocates three storage buffers (`a`, `b` read-only, `c`
//!      read-write) plus a staging buffer for the readback.
//!   3. Fills `a[i] = i`, `b[i] = 2*i` deterministically (matches the
//!      CPU baseline so we can compare).
//!   4. Compiles the WGSL kernel, builds a bind group, dispatches
//!      `ceil(n/workgroup_size)` workgroups.
//!   5. Copies the result buffer back to host RAM and validates every
//!      entry against the CPU baseline (`c[i] == a[i] + b[i] == 3*i`).
//!   6. Prints CPU vs GPU timing + speedup ratio.
//!
//! If no adapter is available (headless CI without LavaPipe / no
//! Vulkan loader / etc.), exits 0 with a clear skip message so the
//! Makefile's `make smoke-gpu` is portable.

use std::env;
use std::fs;
use std::time::Instant;

use wgpu::util::DeviceExt;

/// Parse a single integer value out of a "key=value" config file.
/// Returns `None` if the key is missing or unparseable.
fn cfg_get(cfg: &str, key: &str) -> Option<i64> {
    for line in cfg.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((k, v)) = line.split_once('=') {
            if k.trim() == key {
                return v.trim().parse::<i64>().ok();
            }
        }
    }
    None
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        eprintln!("usage: wgpu_dispatch <shader.wgsl> <sizes.cfg>");
        std::process::exit(2);
    }
    let shader_path = &args[1];
    let cfg_path = &args[2];

    let shader_src = match fs::read_to_string(shader_path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("failed to read shader '{}': {}", shader_path, e);
            std::process::exit(1);
        }
    };
    let cfg_src = match fs::read_to_string(cfg_path) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("failed to read cfg '{}': {}", cfg_path, e);
            std::process::exit(1);
        }
    };

    let n = cfg_get(&cfg_src, "n").unwrap_or(0);
    let workgroup_size = cfg_get(&cfg_src, "workgroup").unwrap_or(64);

    if n <= 0 || workgroup_size <= 0 {
        eprintln!("cfg missing 'n=' or 'workgroup=' (or non-positive)");
        std::process::exit(1);
    }
    let n = n as usize;
    let workgroup_size = workgroup_size as u32;

    println!("=== wgpu_dispatch: vector-add smoke ===");
    println!("  n              = {}", n);
    println!("  workgroup_size = {}", workgroup_size);
    println!("  shader         = {} bytes", shader_src.len());

    // Build deterministic input on the host. `i32` wraps in Rust on
    // release; the WGSL kernel uses the same `i32` semantics so the
    // overflow behaviour matches.
    let a: Vec<i32> = (0..n).map(|i| i as i32).collect();
    let b: Vec<i32> = (0..n).map(|i| (2 * i) as i32).collect();

    // ---- CPU baseline ---------------------------------------------------
    let t_cpu_start = Instant::now();
    let mut c_cpu: Vec<i32> = Vec::with_capacity(n);
    for i in 0..n {
        c_cpu.push(a[i].wrapping_add(b[i]));
    }
    let t_cpu_ms = t_cpu_start.elapsed().as_secs_f64() * 1000.0;

    // ---- GPU dispatch ---------------------------------------------------
    // wgpu's adapter / device handshake is async; pollster turns it
    // into a blocking call.
    match pollster::block_on(run_gpu(&shader_src, &a, &b, workgroup_size)) {
        Ok((c_gpu, t_gpu_ms)) => {
            // Validate every entry. Any miss is a smoke failure.
            let mut mismatches = 0usize;
            for i in 0..n {
                if c_gpu[i] != c_cpu[i] {
                    mismatches += 1;
                }
            }
            if mismatches != 0 {
                println!(
                    "  FAIL: {} / {} elements mismatched (GPU vs CPU)",
                    mismatches, n
                );
                std::process::exit(1);
            }
            // Match the report format from the priority list:
            //   "GPU result matches CPU baseline; CPU=Xms GPU=Yms speedup=Zx"
            let speedup = if t_gpu_ms > 0.0 {
                t_cpu_ms / t_gpu_ms
            } else {
                0.0
            };
            println!(
                "  GPU result matches CPU baseline; CPU={:.2}ms GPU={:.2}ms speedup={:.2}x",
                t_cpu_ms, t_gpu_ms, speedup
            );
            // Spot-check the first / last value so the reader can see
            // we ran the real kernel.
            println!(
                "  spot-check: c[0]={}, c[1]={}, c[{}]={} (expected {}, {}, {})",
                c_gpu[0],
                c_gpu[1],
                n - 1,
                c_gpu[n - 1],
                c_cpu[0],
                c_cpu[1],
                c_cpu[n - 1]
            );
        }
        Err(SkipReason::NoAdapter) => {
            // Exit 0 -- this is a "no GPU here" skip, not a failure.
            // Matches `smoke-windows` (only runs wine on WINE_OK=1)
            // and `smoke-wasm` (only runs node on WASM_OK=1).
            println!("(skip: no wgpu adapter available on this host)");
            println!("  CPU baseline ran fine: CPU={:.2}ms for n={}", t_cpu_ms, n);
            std::process::exit(0);
        }
        Err(SkipReason::Other(msg)) => {
            eprintln!("wgpu dispatch failed: {}", msg);
            std::process::exit(1);
        }
    }
}

/// Local error type that distinguishes "no GPU here" (a clean skip)
/// from any other failure (a real bug).
enum SkipReason {
    NoAdapter,
    Other(String),
}

async fn run_gpu(
    shader_src: &str,
    a: &[i32],
    b: &[i32],
    workgroup_size: u32,
) -> Result<(Vec<i32>, f64), SkipReason> {
    let n = a.len();
    assert_eq!(n, b.len(), "a and b must be same length");
    let buf_bytes = (n * std::mem::size_of::<i32>()) as u64;

    // wgpu instance. `Backends::all()` lets the runtime pick whatever
    // is exposed (Vulkan on Linux, Metal on macOS, D3D12 on Windows,
    // GL on older drivers, WebGPU in browsers).
    let instance = wgpu::Instance::new(wgpu::InstanceDescriptor {
        backends: wgpu::Backends::all(),
        ..Default::default()
    });

    // Request any adapter, low-power preference (we don't need the
    // discrete GPU for a 1M-element vector add).
    let adapter_opt = instance
        .request_adapter(&wgpu::RequestAdapterOptions {
            power_preference: wgpu::PowerPreference::LowPower,
            compatible_surface: None,
            force_fallback_adapter: false,
        })
        .await;
    let adapter = match adapter_opt {
        Some(a) => a,
        None => return Err(SkipReason::NoAdapter),
    };

    let info = adapter.get_info();
    println!(
        "  adapter        = {} ({:?}) backend={:?}",
        info.name, info.device_type, info.backend
    );

    // Request a device + queue with default limits.
    let (device, queue) = adapter
        .request_device(
            &wgpu::DeviceDescriptor {
                label: Some("nova-wgpu-dispatch-device"),
                required_features: wgpu::Features::empty(),
                required_limits: wgpu::Limits::downlevel_defaults(),
            },
            None,
        )
        .await
        .map_err(|e| SkipReason::Other(format!("request_device: {}", e)))?;

    // Compile the WGSL shader. wgpu parses + validates here; any
    // syntax issue surfaces as a panic in the validation callback.
    let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("nova-vector-add-wgsl"),
        source: wgpu::ShaderSource::Wgsl(shader_src.into()),
    });

    // Storage buffer for `a` (read-only). COPY_DST so we can upload
    // the host data; STORAGE so the shader can bind it.
    let a_buf = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
        label: Some("a"),
        contents: bytemuck::cast_slice(a),
        usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_DST,
    });
    let b_buf = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
        label: Some("b"),
        contents: bytemuck::cast_slice(b),
        usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_DST,
    });
    // Output buffer `c`. STORAGE so the shader writes into it,
    // COPY_SRC so we can copy it to the staging buffer for readback.
    let c_buf = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("c"),
        size: buf_bytes,
        usage: wgpu::BufferUsages::STORAGE | wgpu::BufferUsages::COPY_SRC,
        mapped_at_creation: false,
    });
    // Staging buffer. MAP_READ + COPY_DST means we can `map_async`
    // it and then read its contents on the host.
    let staging_buf = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("staging"),
        size: buf_bytes,
        usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST,
        mapped_at_creation: false,
    });

    // Pipeline + bind group layout: one storage buffer per binding.
    let bgl = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
        label: Some("bgl"),
        entries: &[
            wgpu::BindGroupLayoutEntry {
                binding: 0,
                visibility: wgpu::ShaderStages::COMPUTE,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Storage { read_only: true },
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            },
            wgpu::BindGroupLayoutEntry {
                binding: 1,
                visibility: wgpu::ShaderStages::COMPUTE,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Storage { read_only: true },
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            },
            wgpu::BindGroupLayoutEntry {
                binding: 2,
                visibility: wgpu::ShaderStages::COMPUTE,
                ty: wgpu::BindingType::Buffer {
                    ty: wgpu::BufferBindingType::Storage { read_only: false },
                    has_dynamic_offset: false,
                    min_binding_size: None,
                },
                count: None,
            },
        ],
    });
    let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some("pl"),
        bind_group_layouts: &[&bgl],
        push_constant_ranges: &[],
    });
    let pipeline = device.create_compute_pipeline(&wgpu::ComputePipelineDescriptor {
        label: Some("vector-add-pipeline"),
        layout: Some(&pipeline_layout),
        module: &shader,
        entry_point: "main",
        compilation_options: Default::default(),
    });
    let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some("bg"),
        layout: &bgl,
        entries: &[
            wgpu::BindGroupEntry {
                binding: 0,
                resource: a_buf.as_entire_binding(),
            },
            wgpu::BindGroupEntry {
                binding: 1,
                resource: b_buf.as_entire_binding(),
            },
            wgpu::BindGroupEntry {
                binding: 2,
                resource: c_buf.as_entire_binding(),
            },
        ],
    });

    // ---- Timed dispatch ------------------------------------------------
    let t_gpu_start = Instant::now();

    let mut encoder = device.create_command_encoder(&wgpu::CommandEncoderDescriptor {
        label: Some("cmd"),
    });
    {
        let mut cpass = encoder.begin_compute_pass(&wgpu::ComputePassDescriptor {
            label: Some("vector-add-pass"),
            timestamp_writes: None,
        });
        cpass.set_pipeline(&pipeline);
        cpass.set_bind_group(0, &bind_group, &[]);
        // ceil(n / workgroup_size) workgroups in the x dimension.
        let n_workgroups = ((n as u32) + workgroup_size - 1) / workgroup_size;
        cpass.dispatch_workgroups(n_workgroups, 1, 1);
    }
    // Copy result -> staging buffer.
    encoder.copy_buffer_to_buffer(&c_buf, 0, &staging_buf, 0, buf_bytes);
    queue.submit(Some(encoder.finish()));

    // Map staging buffer and wait for the GPU to finish.
    let slice = staging_buf.slice(..);
    let (tx, rx) = std::sync::mpsc::channel();
    slice.map_async(wgpu::MapMode::Read, move |res| {
        let _ = tx.send(res);
    });
    // poll until the map completes. wgpu requires the device to be
    // polled to drive callbacks.
    device.poll(wgpu::Maintain::Wait);
    let map_result = rx
        .recv()
        .map_err(|e| SkipReason::Other(format!("staging recv: {}", e)))?;
    map_result.map_err(|e| SkipReason::Other(format!("buffer map: {:?}", e)))?;

    let mapped = slice.get_mapped_range();
    let out: Vec<i32> = bytemuck::cast_slice::<u8, i32>(&mapped).to_vec();
    drop(mapped);
    staging_buf.unmap();

    let t_gpu_ms = t_gpu_start.elapsed().as_secs_f64() * 1000.0;
    Ok((out, t_gpu_ms))
}
