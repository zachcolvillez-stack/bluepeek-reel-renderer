// GitHub Actions worker: claim one queued job, render real browser media, upload
// public assets to Vercel Blob, then make the review card ready in the dashboard.
import { createClient } from "@supabase/supabase-js";
import { put } from "@vercel/blob";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
if (!supabaseUrl || !serviceKey || !process.env.BLOB_READ_WRITE_TOKEN) {
  throw new Error("Worker needs NEXT_PUBLIC_SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and BLOB_READ_WRITE_TOKEN.");
}
const db = createClient(supabaseUrl, serviceKey, { auth: { persistSession: false, autoRefreshToken: false } });

async function main() {
  const stale = new Date(Date.now() - 160 * 60_000).toISOString();
  await db.from("social_showcases").update({ status: "failed", error: "Rendering worker timed out. Create a new showcase.", updated_at: new Date().toISOString() })
    .eq("status", "running").lt("updated_at", stale);

  const { data: waiting, error: queueError } = await db.from("social_showcases")
    .select("id,business_name,site_url,accent_color,case_study,reel_hook,created_by,render_attempt,reel_meta").eq("status", "queued")
    .order("created_at", { ascending: true }).limit(1);
  if (queueError) throw queueError;
  if (!waiting?.length) { console.log("No showcase jobs queued."); return; }
  const job = waiting[0];
  const { data: claimed, error: claimError } = await db.from("social_showcases")
    .update({ status: "running", error: null, updated_at: new Date().toISOString() })
    .eq("id", job.id).eq("status", "queued").select("id").maybeSingle();
  if (claimError) throw claimError;
  if (!claimed) { console.log("Another worker claimed the job."); return; }

  // One uploaded track per Reel, picked from the dashboard's music library.
  const { data: tracks } = await db.from("social_reel_tracks").select("name,url,start_seconds").eq("active", true).order("id");
  const reelMusic = tracks?.length ? tracks[Math.floor(Math.random() * tracks.length)] : null;

  const dir = mkdtempSync(join(tmpdir(), "bluepeek-showcase-"));
  try {
    const manifest = join(dir, "manifest.json");
    const plateOverride = (job.reel_meta as { plate_override?: string } | null)?.plate_override;
    writeFileSync(manifest, JSON.stringify({ ...job, reel_music: reelMusic, ...(plateOverride ? { reel_plate: plateOverride } : {}) }));
    if (job.created_by === "social-agent") {
      // Agent-found sites get reviewed before the long render (Zach, 15 Nov:
      // three of five reels "don't scroll through a website").
      const verdict = await review(job, manifest, dir);
      if (!verdict.pass) {
        await db.from("social_showcases").update({ status: "failed", error: `Reviewer: ${verdict.reason}`.slice(0, 1000), updated_at: new Date().toISOString() })
          .eq("id", job.id).eq("status", "running");
        console.log(`Rejected by the reviewer: ${verdict.reason}`);
        return;
      }
      console.log(`Reviewer passed it: ${verdict.reason}`);
    }
    const render = spawnSync("python", ["scripts/render-social-showcase.py", manifest, dir], {
      stdio: "inherit", timeout: 130 * 60_000,
    });
    if (render.error) throw render.error;
    if (render.status !== 0) throw new Error(`Renderer exited with code ${render.status}`);
    if (job.created_by === "social-agent") {
      // The finished Reel gets looked over before it can post (Instagram runs
      // on autopilot): squished screens, unreadable hooks, broken frames.
      const verdict = await finalCheck(job, dir);
      if (!verdict.pass) {
        // Regenerate (Zach, 16 Nov): one more render on a different background
        // before giving up on the site. The agent replaces sites that fail twice.
        const attempt = Number(job.render_attempt ?? 0);
        if (attempt < 1) {
          let usedPlate: string | null = null;
          try { usedPlate = JSON.parse(readFileSync(join(dir, "reel-meta.json"), "utf8")).plate ?? null; } catch { /* no meta */ }
          const plates = (JSON.parse(readFileSync("scripts/macbook/plates.json", "utf8")).plates as { id: string }[]).map((p) => p.id);
          const others = plates.filter((p) => p !== usedPlate);
          const next = others[Math.floor(Math.random() * others.length)] ?? null;
          await db.from("social_showcases").update({
            status: "queued", render_attempt: attempt + 1, error: `Re-rendering after final check: ${verdict.reason}`.slice(0, 1000),
            reel_meta: { ...((job.reel_meta as Record<string, unknown>) ?? {}), plate_override: next }, updated_at: new Date().toISOString(),
          }).eq("id", job.id).eq("status", "running");
          console.log(`Final check held it; re-rendering on ${next}: ${verdict.reason}`);
          return;
        }
        await db.from("social_showcases").update({ status: "failed", error: `Final check: ${verdict.reason}`.slice(0, 1000), updated_at: new Date().toISOString() })
          .eq("id", job.id).eq("status", "running");
        console.log(`Held by the final check: ${verdict.reason}`);
        return;
      }
      console.log(`Final check passed it: ${verdict.reason}`);
    }

    const upload = async (filename: string, contentType: string) => {
      const blob = await put(`social-showcases/${job.id}/${filename}`, readFileSync(join(dir, filename)), {
        access: "public", contentType, addRandomSuffix: false, allowOverwrite: true,
      });
      return blob.url;
    };
    const imagePng = await upload("showcase.png", "image/png");
    const imageJpeg = await upload("showcase.jpg", "image/jpeg");
    const reel = await upload("reel.mp4", "video/mp4");
    const caseStudy = job.case_study ? {
      case_study_png_url: await upload("case-study.png", "image/png"),
      case_study_jpeg_url: await upload("case-study.jpg", "image/jpeg"),
    } : {};
    const metadata = JSON.parse(readFileSync(join(dir, "metadata.json"), "utf8")) as { accent_color: string };
    let reelMeta: Record<string, unknown> | null = null;
    try { reelMeta = JSON.parse(readFileSync(join(dir, "reel-meta.json"), "utf8")); } catch { /* older renderer */ }
    // The 4K master, for platforms posted by hand (YouTube Shorts takes 4K).
    if (reelMeta && existsSync(join(dir, "reel-4k.mp4"))) reelMeta.reel_4k_url = await upload("reel-4k.mp4", "video/mp4");
    const { error } = await db.from("social_showcases").update({
      status: "ready", image_png_url: imagePng, image_jpeg_url: imageJpeg,
      reel_url: reel, ...caseStudy, reel_meta: reelMeta, accent_color: metadata.accent_color, updated_at: new Date().toISOString(),
    }).eq("id", job.id).eq("status", "running");
    if (error) throw error;
    console.log(`Ready: ${job.id}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await db.from("social_showcases").update({ status: "failed", error: message.slice(0, 1000), updated_at: new Date().toISOString() })
      .eq("id", job.id).eq("status", "running");
    throw error;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

type Verdict = { pass: boolean; reason: string; sections_seen?: number; wow?: number };

/** Preflight screenshots → Blob → the dashboard's reviewer (which holds the Claude key). */
async function review(job: { id: string; site_url: string }, manifest: string, dir: string): Promise<Verdict> {
  const pre = spawnSync("python", ["scripts/render-social-showcase.py", manifest, dir], {
    stdio: ["ignore", "inherit", "pipe"], timeout: 10 * 60_000, env: { ...process.env, SHOWCASE_PREFLIGHT: "1" },
  });
  if (pre.status !== 0) {
    const err = String(pre.stderr ?? "").trim().split("\n").pop() ?? "";
    console.error(String(pre.stderr ?? ""));
    return { pass: false, reason: /too heavy/.test(err) ? err.replace(/^\w*Error: /, "") : "The site wouldn't load for a recording." };
  }
  const stats = JSON.parse(readFileSync(join(dir, "preflight.json"), "utf8"));
  const frames = [];
  for (let i = 0; i < 8; i++) {
    const name = `${String(i).padStart(2, "0")}.jpg`;
    const blob = await put(`social-showcases/${job.id}/preflight/${name}`, readFileSync(join(dir, "preflight", name)), {
      access: "public", contentType: "image/jpeg", addRandomSuffix: false, allowOverwrite: true,
    });
    frames.push(blob.url);
  }
  const res = await fetch(`${process.env.DASHBOARD_URL || "https://bluepeekdashboard.com.au"}/api/social/review-preflight`, {
    method: "POST",
    headers: { authorization: `Bearer ${serviceKey}`, "content-type": "application/json" },
    body: JSON.stringify({ site_url: job.site_url, frames, stats }),
    signal: AbortSignal.timeout(150_000),
  });
  const body = (await res.json().catch(() => ({}))) as Verdict & { error?: string };
  // Fail closed: no review, no render. The agent finds another site.
  if (!res.ok) return { pass: false, reason: `The reviewer couldn't check it (${body.error ?? res.status}).` };
  return body;
}

/** 8 frames of the finished Reel → Blob → the dashboard's final check. Fails closed. */
async function finalCheck(job: { id: string; site_url: string }, dir: string): Promise<Verdict> {
  const reel = join(dir, "reel.mp4");
  const probe = spawnSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", reel], { encoding: "utf8" });
  const duration = Number(String(probe.stdout).trim());
  if (!duration) return { pass: false, reason: "Couldn't read the finished Reel." };
  const frames = [];
  for (let i = 0; i < 8; i++) {
    const name = `${String(i).padStart(2, "0")}.jpg`;
    const out = join(dir, `final-${name}`);
    const at = (duration * (i + 0.5)) / 8;
    const ff = spawnSync("ffmpeg", ["-v", "error", "-y", "-ss", at.toFixed(2), "-i", reel, "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "3", out]);
    if (ff.status !== 0 || !existsSync(out)) return { pass: false, reason: "Couldn't take frames from the finished Reel." };
    const blob = await put(`social-showcases/${job.id}/final/${name}`, readFileSync(out), {
      access: "public", contentType: "image/jpeg", addRandomSuffix: false, allowOverwrite: true,
    });
    frames.push(blob.url);
  }
  const res = await fetch(`${process.env.DASHBOARD_URL || "https://bluepeekdashboard.com.au"}/api/social/review-preflight`, {
    method: "POST",
    headers: { authorization: `Bearer ${serviceKey}`, "content-type": "application/json" },
    body: JSON.stringify({ site_url: job.site_url, frames, stage: "final" }),
    signal: AbortSignal.timeout(150_000),
  }).catch(() => null);
  const body = (await res?.json().catch(() => ({}))) as Verdict & { error?: string };
  if (!res?.ok) return { pass: false, reason: `The final check couldn't run (${body?.error ?? res?.status ?? "no response"}).` };
  return body;
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
