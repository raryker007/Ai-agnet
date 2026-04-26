import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from agent.core import FileAgent

load_dotenv()
console = Console()


def main() -> None:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        console.print("[bold red]Error:[/bold red] GROQ_API_KEY is not set. Copy .env.example to .env and add your key.")
        sys.exit(1)

    working_dir = os.getenv("WORKING_DIRECTORY")
    if working_dir and not Path(working_dir).is_dir():
        console.print(f"[bold red]Error:[/bold red] WORKING_DIRECTORY '{working_dir}' does not exist.")
        sys.exit(1)

    agent = FileAgent(api_key=api_key, working_directory=working_dir)

    console.print(Panel.fit(
        "[bold green]Groq File Agent[/bold green]\n"
        f"[dim]Working directory: {agent.working_directory}[/dim]\n"
        "[dim]Type 'exit' to quit  |  'log' to view operation history[/dim]",
        border_style="green",
    ))

    while True:
        try:
            user_input = Prompt.ask("\n[bold blue]You[/bold blue]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Interrupted.[/yellow]")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "q"}:
            break
        if user_input.lower() == "log":
            agent.display_operation_log()
            continue

        try:
            response = agent.run(user_input)
            console.print(f"\n[bold green]Agent:[/bold green] {response}")
        except Exception as exc:
            console.print(f"\n[bold red]Error:[/bold red] {exc}")

    agent.display_operation_log()
    console.print("[dim]Goodbye.[/dim]")


if __name__ == "__main__":
    main()
