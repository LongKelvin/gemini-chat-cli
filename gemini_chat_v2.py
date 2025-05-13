import os
import re
import json
import docx
import pandas as pd
import configparser
import keyring
import datetime
from rich import print as rprint
from rich.console import Console
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
import google.generativeai as genai
from PyPDF2 import PdfReader
from cryptography.fernet import Fernet
from email.parser import Parser
from email.policy import default
import glob
import pyperclip
import keyboard
import argparse
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
import openpyxl

console = Console()
all_code_blocks = []
chat_history = []  # In-memory conversation history for the current session
current_session_file = None  # Current history file path

# --- API Key Management (unchanged) ---
def encrypt_api_key(api_key, key):
    f = Fernet(key)
    return f.encrypt(api_key.encode()).decode()

def decrypt_api_key(encrypted_api_key, key):
    f = Fernet(key)
    return f.decrypt(encrypted_api_key.encode()).decode()

def get_api_key():
    config = configparser.ConfigParser()
    if not os.path.exists('config.ini'):
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

api_key = get_api_key()
genai.configure(api_key=api_key)

# --- History Management Functions ---
def get_history_dir():
    """Returns the path to the histories directory."""
    return os.path.join(os.path.dirname(__file__), 'histories')

def ensure_directory_exists(file_path):
    """Ensure the directory for the given file path exists."""
    directory = os.path.dirname(file_path)
    if directory and not os.path.exists(directory):
        try:
            os.makedirs(directory)
            rprint(f"[bold blue]Created directory: {directory}[/bold blue]")
        except Exception as e:
            rprint(f"[bold red]Error creating directory {directory}: {str(e)}[/bold red]")
            return False
    return True

def generate_session_filename():
    """Generates a unique filename for the session based on timestamp."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(get_history_dir(), f"{timestamp}_conversation.json")

def load_history(file_path):
    """Loads conversation history from a JSON file."""
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        rprint(f"[bold red]Error loading history from {file_path}: {str(e)}[/bold red]")
        return []

def save_history():
    """Saves the current in-memory history to the session file."""
    global chat_history, current_session_file
    if not current_session_file:
        return
    try:
        ensure_directory_exists(current_session_file)
        with open(current_session_file, 'w', encoding='utf-8') as f:
            json.dump(chat_history, f, indent=2)
    except Exception as e:
        rprint(f"[bold red]Error saving history to {current_session_file}: {str(e)}[/bold red]")

def summarize_history():
    """Summarizes the current session's history using Gemini API."""
    if not chat_history:
        return "[No history to summarize]"
    
    history_text = "\n".join(
        f"{entry['role'].capitalize()}: {entry['parts'][0]}" for entry in chat_history
    )
    summary_prompt = f"Summarize the key topics discussed in this conversation:\n{history_text}"
    return generate_response(summary_prompt, use_history=False)

def truncate_history(max_exchanges=20):
    """Truncates history to the last max_exchanges, summarizing older messages."""
    global chat_history
    if len(chat_history) <= max_exchanges * 2:  # Each exchange is user + model
        return
    
    older_history = chat_history[:-max_exchanges * 2]
    recent_history = chat_history[-max_exchanges * 2:]
    
    # Summarize older history
    older_text = "\n".join(f"{entry['role'].capitalize()}: {entry['parts'][0]}" for entry in older_history)
    summary_prompt = f"Summarize this conversation history into a concise context:\n{older_text}"
    summary = generate_response(summary_prompt, use_history=False)
    
    # Replace older history with summary
    chat_history = [
        {"role": "model", "parts": [f"Summary of earlier conversation: {summary}"], "timestamp": datetime.datetime.now().isoformat()}
    ] + recent_history
    save_history()
    rprint("[bold yellow]History truncated and summarized to stay within token limits.[/bold yellow]")

def check_token_limit():
    """Warns if history is approaching token limits (heuristic: ~800k chars)."""
    history_text = json.dumps(chat_history)
    if len(history_text) > 800000:  # Rough estimate for ~80% of 1M tokens
        rprint("[bold yellow]Warning: Conversation history is large. Summarizing older messages...[/bold yellow]")
        truncate_history()

# --- File Handling Functions (unchanged) ---
def detect_file_in_prompt(prompt):
    """Detects a file path, converts it to an absolute path, and checks if it exists."""
    match = re.search(r'([a-zA-Z]:[\\/](?:[\w\s\-\._()]+[\\/]?)+)', prompt)
    if match:
        file_path = match.group(0)
        absolute_path = os.path.abspath(file_path)
        if os.path.exists(absolute_path):
            return absolute_path
        else:
            rprint(f"[bold yellow]Detected path '{file_path}' (absolute: '{absolute_path}') does not exist.[/bold yellow]")
            return None
    return None

def summarize_text(text, max_length=5000):
    """Summarizes long text using Gemini."""
    if len(text) <= max_length:
        return text
    summary_prompt = f"Summarize the following text:\n{text[:max_length]}..."
    return generate_response(summary_prompt, use_history=False)

def extract_from_json(file_path, ai_suggestion=None):
    """Extracts data from a JSON file, optionally using AI suggestions."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            if isinstance(data, dict) and ai_suggestion:
                keys_to_extract = [key.strip() for key in ai_suggestion.split(",")]
                return {key: data.get(key, "N/A") for key in keys_to_extract}
            return json.dumps(data, indent=2)
    except Exception as e:
        return f"[Error reading JSON: {str(e)}]"

def extract_from_excel(file_path, ai_suggestion=None):
    """Extracts data from Excel files, handling sheets and columns."""
    try:
        df = pd.read_excel(file_path, sheet_name=None)
        if ai_suggestion and ":" in ai_suggestion:
            sheet_name, column_name = ai_suggestion.split(":", 1)
            sheet_name = sheet_name.strip()
            column_name = column_name.strip()
            if sheet_name in df and column_name in df[sheet_name].columns:
                return df[sheet_name][column_name].to_string(index=False)
            return f"[Sheet '{sheet_name}' or Column '{column_name}' not found]"
        first_sheet_name = list(df.keys())[0]
        return df[first_sheet_name].to_string(index=False)
    except Exception as e:
        return f"[Error reading Excel: {str(e)}]"

def read_file_content(file_path, ai_suggestion=None):
    """Reads content from various file types, using AI suggestions where applicable."""
    if not os.path.exists(file_path):
        return "[Error] File not found."

    _, ext = os.path.splitext(file_path)
    try:
        if ext in ['.txt', '.py', '.cs', '.js']:
            with open(file_path, 'r', encoding='utf-8') as file:
                return summarize_text(file.read())
        elif ext == '.json':
            return extract_from_json(file_path, ai_suggestion)
        elif ext in ['.xlsx', '.xls']:
            return extract_from_excel(file_path, ai_suggestion)
        elif ext == '.csv':
            df = pd.read_csv(file_path)
            return df.head().to_string(index=False)
        elif ext == '.log':
            with open(file_path, 'r', encoding='utf-8') as file:
                lines = file.readlines()
                errors = [line for line in lines if "ERROR" in line or "WARN" in line]
                return "\n".join(errors[:10]) if errors else "[No errors/warnings found]"
        elif ext == '.pdf':
            reader = PdfReader(file_path)
            content = "\n".join(page.extract_text() for page in reader.pages if page.extract_text())
            return summarize_text(content)
        elif ext == '.docx':
            doc = docx.Document(file_path)
            full_text = [paragraph.text for paragraph in doc.paragraphs]
            return summarize_text('\n'.join(full_text))
        else:
            return f"[Unsupported file type: {ext}]"
    except Exception as e:
        return f"[Error reading file: {str(e)}]"

# --- Core Gemini Interaction ---
def generate_response(prompt, model_name="gemini-2.0-flash", max_retries=5, retry_delay=5, use_history=True):
    """Generates a response, handling rate limits with retries."""
    model = genai.GenerativeModel(model_name)
    for attempt in range(max_retries):
        try:
            if use_history:
                # Format history for Gemini API
                history = [{"role": entry["role"], "parts": entry["parts"]} for entry in chat_history]
                chat = model.start_chat(history=history)
                response = chat.send_message(prompt)
            else:
                response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            if "429" in str(e):  # Rate limit error
                rprint(f"[bold yellow]Rate limit exceeded (attempt {attempt + 1}/{max_retries}). Waiting {retry_delay} seconds...[/bold yellow]")
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                rprint(f"[bold red]An API error occurred: {e}[/bold red]")
                return f"[Error] An API error occurred: {e}"
    rprint(f"[bold red]Max retries exceeded. Failed to generate response.[/bold red]")
    return "[Error] Max retries exceeded"

def process_prompt_with_file(prompt):
    """Processes prompts that include file paths."""
    file_path = detect_file_in_prompt(prompt)
    if not file_path:
        return generate_response(prompt)

    placeholder = "<<<FILE_PATH>>>"
    prompt_with_placeholder = prompt.replace(file_path, placeholder)
    intent_prompt = (
        f"The user mentions a file in their prompt. Here's the prompt, "
        f"with the file path replaced by '{placeholder}':\n\n"
        f"'{prompt_with_placeholder}'\n\n"
        f"What specific information or action is the user likely requesting "
        f"regarding the file? Examples: 'summarize', 'extract key points', "
        f"'find errors', 'get the value of X', 'list column names'. "
        f"Be concise and specific."
    )

    ai_intent = generate_response(intent_prompt, use_history=False)
    rprint(f"[bold blue]AI Intent Analysis:[/bold blue] {ai_intent}")
    file_content = read_file_content(file_path, ai_intent)
    final_prompt = (
        f"User's request (with file path replaced by '{placeholder}'):\n"
        f"{prompt_with_placeholder}\n\n"
        f"Actual file path: {file_path}\n\n"
        f"Extracted File Content (or error):\n{file_content}\n\n"
        f"Based on the file content and AI's interpretation of the user's "
        f"intent ({ai_intent}), please fulfill the user's request."
    )
    return generate_response(final_prompt)

# --- Command Handling ---
def handle_command(prompt):
    """Handles history-related commands."""
    global chat_history, current_session_file

    if prompt.startswith("/history"):
        parts = prompt.split(maxsplit=1)
        limit = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        history_files = glob.glob(os.path.join(get_history_dir(), "*_conversation.json"))
        if not history_files:
            rprint("[bold yellow]No conversation history files found.[/bold yellow]")
            return True
        
        rprint("[bold blue]Available Conversation Histories:[/bold blue]")
        for file in history_files:
            console.print(f"- {os.path.basename(file)}")
        if limit:
            history = load_history(current_session_file)[:limit * 2] if current_session_file else []
            if history:
                rprint(f"[bold blue]Last {limit} exchanges in current session:[/bold blue]")
                for entry in history:
                    preview = entry["parts"][0][:100] + ("..." if len(entry["parts"][0]) > 100 else "")
                    console.print(f"{entry['timestamp']} [{entry['role'].capitalize()}]: {preview}")
        return True

    elif prompt.startswith("/summarize"):
        summary = summarize_history()
        console.print(Markdown(f"**Conversation Summary:**\n{summary}"))
        return True

    elif prompt.startswith("/clear"):
        if current_session_file and os.path.exists(current_session_file):
            os.remove(current_session_file)
            rprint(f"[bold green]Cleared history file: {current_session_file}[/bold green]")
        chat_history.clear()
        current_session_file = None
        rprint("[bold green]Started new session.[/bold green]")
        return True

    elif prompt.startswith("/new_session"):
        chat_history.clear()
        current_session_file = generate_session_filename()
        rprint(f"[bold green]Started new session: {os.path.basename(current_session_file)}[/bold green]")
        save_history()
        return True

    return False

# --- UI and Interaction ---
def display_formatted_response(response_text):
    """Identifies and formats code and markdown in the response."""
    global all_code_blocks
    segments = response_text.split("```")
    for i, segment in enumerate(segments):
        if i % 2 == 0:
            console.print(Markdown(segment.strip()))
        else:
            lines = segment.split('\n')
            language = lines[0].strip() if lines[0].strip() else 'python'
            code = '\n'.join(lines[1:])
            syntax = Syntax(code, language, theme="monokai", line_numbers=True)
            console.print(Panel(syntax, title=f"[bold]{language.capitalize()} Code[/bold]", expand=True))
            all_code_blocks.append(code)

def get_user_prompt():
    """Prompts the user for input."""
    try:
        prompt = console.input("[bold green]Enter your prompt (or 'quit'/'exit' to terminate): [/bold green]")
        if prompt.lower() in ['quit', 'exit']:
            quit_application()
        return prompt
    except KeyboardInterrupt:
        quit_application()

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
    """Gracefully exits the application."""
    rprint("\n[bold red]Exiting the program. Goodbye![/bold red]")
    keyboard.unhook_all()
    exit(0)

# --- Main Function ---
def main():
    global chat_history, current_session_file
    parser = argparse.ArgumentParser(description='Enhanced Gemini Chat with File Processing and History')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    chat_parser = subparsers.add_parser('chat', help='Start interactive chat mode')
    args = parser.parse_args()

    keyboard.add_hotkey('ctrl+alt+c', copy_to_clipboard)
    
    # Initialize new session
    current_session_file = generate_session_filename()
    chat_history = load_history(current_session_file)
    
    rprint("[bold]Welcome to the Enhanced Gemini Chat![/bold]")
    rprint("[bold cyan]Press Ctrl+Alt+C to copy all generated code blocks.[/bold cyan]")
    rprint("[bold cyan]Press Ctrl+C or type 'quit'/'exit' to terminate.[/bold cyan]")
    rprint("[bold cyan]Commands: /history [N], /summarize, /clear, /new_session[/bold cyan]")

    while True:
        try:
            prompt = get_user_prompt()
            if not prompt:
                continue

            if handle_command(prompt):
                continue

            rprint("[bold yellow]Generating response...[/bold yellow]")
            response = process_prompt_with_file(prompt)
            display_formatted_response(response)

            # Append to history
            chat_history.append({
                "role": "user",
                "parts": [prompt],
                "timestamp": datetime.datetime.now().isoformat()
            })
            chat_history.append({
                "role": "model",
                "parts": [response],
                "timestamp": datetime.datetime.now().isoformat()
            })
            save_history()
            check_token_limit()

        except Exception as e:
            console.print(f"[bold red]An error occurred: {e}[/bold red]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        quit_application()