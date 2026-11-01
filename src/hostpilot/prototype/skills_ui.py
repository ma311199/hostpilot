"""Local Gradio editor for user-managed Skill packages."""

from __future__ import annotations

import json
from typing import Any

from hostpilot.skills.registry import SkillRegistry


def create_skills_demo(registry: SkillRegistry) -> Any:
    """Build a local Skill catalog/editor without launching a web server."""
    import gradio as gr

    def refresh() -> str:
        catalog = registry.discover()
        data = [
            {
                "name": skill.name,
                "description": skill.description,
                "content_hash": skill.summary.content_hash,
            }
            for skill in catalog.skills
        ]
        return json.dumps(
            {"skills": data, "issues": [issue.message for issue in catalog.issues]},
            ensure_ascii=False,
            indent=2,
        )

    def load(name: str) -> tuple[str, str, str]:
        try:
            skill = registry.load(name)
        except (KeyError, OSError) as error:
            return "", "", str(error)
        return skill.description, skill.instructions, f"Loaded {skill.name}."

    def save(name: str, description: str, instructions: str) -> tuple[str, str]:
        try:
            metadata = registry.load(name).metadata if registry.contains(name) else None
            skill = registry.save(
                name,
                description,
                instructions,
                overwrite=metadata is not None,
                metadata=metadata,
            )
        except (OSError, ValueError) as error:
            return refresh(), str(error)
        return refresh(), f"Saved {skill.name} ({skill.summary.content_hash[:12]})."

    with gr.Blocks(title="HostPilot Skills") as demo:
        gr.Markdown("# HostPilot 本地 Skill 管理原型")
        gr.Markdown(f"Skill 根目录：{registry.root}")
        with gr.Row():
            name = gr.Textbox(label="Skill 名称", placeholder="meeting-notes")
            description = gr.Textbox(label="用途说明")
        instructions = gr.Textbox(
            label="SKILL.md 指令正文",
            lines=18,
            placeholder="使用 Markdown 编写 Skill 指令。",
        )
        with gr.Row():
            load_button = gr.Button("加载")
            save_button = gr.Button("保存")
            refresh_button = gr.Button("刷新目录")
        status = gr.Markdown()
        catalog = gr.Code(label="本地 Skill 目录", language="json", value=refresh())

        load_button.click(load, inputs=[name], outputs=[description, instructions, status])
        save_button.click(
            save,
            inputs=[name, description, instructions],
            outputs=[catalog, status],
        )
        refresh_button.click(refresh, outputs=[catalog])

    return demo


def launch_skills_demo(registry: SkillRegistry) -> None:
    """Launch the prototype on loopback only."""
    create_skills_demo(registry).launch(server_name="127.0.0.1", share=False)
