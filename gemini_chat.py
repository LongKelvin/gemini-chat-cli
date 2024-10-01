import os
import configparser
import keyring
from cryptography.fernet import Fernet
import google.generativeai as genai
from rich.console import Console
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.panel import Panel
from rich import print as rprint
import pyperclip
import keyboard
import signal

console = Console()
all_code_blocks = []

def encrypt_api_key(api_key, key):
    f = Fernet(key)
    return f.encrypt(api_key.encode()).decode()

def decrypt_api_key(encrypted_api_key, key):
    f = Fernet(key)
    return f.decrypt(encrypted_api_key.encode()).decode()

def get_api_key():
    config = configparser.ConfigParser()
    if not os.path.exists('config.ini'):
        # First-time setup
        api_key = console.input("[bold yellow]Enter your Gemini API key: [/bold yellow]")
        encryption_key = Fernet.generate_key()
        keyring.set_password("gemini_chat", "encryption_key", encryption_key.decode())
        
        encrypted_api_key = encrypt_api_key(api_key, encryption_key)
        
        config['API'] = {'encrypted_key': encrypted_api_key}
        with open('config.ini', 'w') as configfile:
            config.write(configfile)
    else:
        config.read('config.ini')
    
    encrypted_api_key = config['API']['encrypted_key']
    encryption_key = keyring.get_password("gemini_chat", "encryption_key").encode()
    return decrypt_api_key(encrypted_api_key, encryption_key)

# Configure Gemini API
api_key = get_api_key()
genai.configure(api_key=api_key)

def generate_response(prompt, model_name="gemini-1.5-pro"):
    """Generates a response using the given prompt and model."""
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(prompt)
    return response.text

def copy_to_clipboard():
    """Copies all generated code blocks to clipboard."""
    global all_code_blocks
    if all_code_blocks:
        all_code = "\n\n".join(all_code_blocks)
        pyperclip.copy(all_code)
        rprint("\n[bold green]All code blocks copied to clipboard![/bold green]")
    else:
        rprint("\n[bold yellow]No code available to copy.[/bold yellow]")

def quit_application(signum=None, frame=None):
    rprint("\n[bold red]Exiting the program. Goodbye![/bold red]")
    keyboard.unhook_all()
    exit(0)

def display_formatted_response(response_text):
    """Identifies and formats code and markdown in the response."""
    global all_code_blocks
    segments = response_text.split("```")
    for i, segment in enumerate(segments):
        if i % 2 == 0:  # Markdown section
            console.print(Markdown(segment.strip()))
        else:  # Code block
            lines = segment.split('\n')
            language = lines[0].strip() if lines[0].strip() else 'python'
            code = '\n'.join(lines[1:])
            syntax = Syntax(code, language, theme="monokai", line_numbers=True)
            console.print(Panel(syntax, title=f"[bold]{language.capitalize()} Code[/bold]", expand=True))
            all_code_blocks.append(code)

def get_user_prompt():
    """Prompts the user for input and returns it."""
    try:
        prompt = console.input("[bold green]Enter your prompt (or 'quit'/'exit' to terminate): [/bold green]")
        if prompt.lower() in ['quit', 'exit']:
            quit_application()
        return prompt
    except KeyboardInterrupt:
        quit_application()

def main():
    rprint("[bold]Welcome to the Enhanced Gemini Chat![/bold]")
    rprint("[bold cyan]Press Ctrl+Alt+C to copy all generated code blocks.[/bold cyan]")
    rprint("[bold cyan]Press Ctrl+C or type 'quit'/'exit' to terminate the program.[/bold cyan]")
    
    # Set up the hotkey listener
    keyboard.add_hotkey('ctrl+alt+c', copy_to_clipboard)
    
    while True:
        try:
            prompt = get_user_prompt()
            if not prompt:
                continue

            rprint("[bold yellow]Generating response...[/bold yellow]")
            response = generate_response(prompt)
            display_formatted_response(response)
        except Exception as e:
            console.print(f"[bold red]An error occurred: {str(e)}[/bold red]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        quit_application()