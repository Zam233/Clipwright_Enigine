import { spawn } from "node:child_process";
import path from "node:path";
import { z } from "zod";
import { tool } from "@opencode-ai/plugin/tool";

export default async function describeImagePlugin() {
  return {
    tool: {
      describe_image: tool({
        description:
          "Describe a local image file in Chinese using a vision LLM (Agent toolchain). " +
          "Pass a path to an image (png/jpg/jpeg/gif/webp); optionally pass a custom prompt. " +
          "Requires Python and network access to the vision API gateway.",
        args: {
          image_path: z
            .string()
            .describe("Path to the image file to describe."),
          prompt: z
            .string()
            .optional()
            .describe("Optional custom prompt for describing the image."),
        },
        async execute(args, context) {
          const scriptPath = path.join(
            context.worktree || context.directory,
            ".opencode",
            "scripts",
            "describe_image.py",
          );

          const scriptArgs = [scriptPath, args.image_path];
          if (args.prompt) {
            scriptArgs.push("--prompt", args.prompt);
          }

          return new Promise((resolve, reject) => {
            let stdout = "";
            let stderr = "";
            const child = spawn("python", scriptArgs, {
              cwd: context.directory,
              signal: context.abort,
              windowsHide: true,
            });

            child.stdout.on("data", (chunk: Buffer) => {
              stdout += chunk.toString();
            });
            child.stderr.on("data", (chunk: Buffer) => {
              stderr += chunk.toString();
            });

            child.on("error", (err) => {
              reject(
                new Error(
                  "Failed to start describe_image.py: " +
                    err.message +
                    (stderr ? "\nstderr: " + stderr : ""),
                ),
              );
            });

            child.on("close", (code) => {
              if (code === 0) {
                resolve({ title: "describe_image", output: stdout.trim() });
              } else {
                reject(
                  new Error(
                    "describe_image.py exited with code " +
                      code +
                      "\nstderr: " +
                      (stderr.trim() || "(empty)") +
                      "\nstdout: " +
                      stdout.trim(),
                  ),
                );
              }
            });
          });
        },
      }),
    },
  };
}
