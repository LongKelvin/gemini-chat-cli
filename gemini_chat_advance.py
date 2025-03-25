import os
import re
import json
import docx
import pandas as pd
import configparser
import keyring
import datetime
from pptx import Presentation
from rich import print as rprint
from rich.console import Console
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.panel import Panel
import google.generativeai as genai
from PyPDF2 import PdfReader
from cryptography.fernet import Fernet
import pyperclip
import keyboard
import argparse
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
import openpyxl
import time
from google.api_core import exceptions as google_exceptions  # Import google_exceptions
import shutil
import difflib

console = Console()
all_code_blocks = []

# --- API Key Management --- (Same as before, no changes)


def encrypt_api_key(api_key, key):
    f = Fernet(key)
    return f.encrypt(api_key.encode()).decode()


def decrypt_api_key(encrypted_api_key, key):
    f = Fernet(key)
    return f.decrypt(encrypted_api_key.encode(), ttl=None).decode()


def get_api_key():
    config = configparser.ConfigParser()
    if not os.path.exists("config.ini"):
        api_key = console.input(
            "[bold yellow]Enter your Gemini API key: [/bold yellow]")
        encryption_key = Fernet.generate_key()
        keyring.set_password("gemini_chat", "encryption_key",
                             encryption_key.decode())
        encrypted_api_key = encrypt_api_key(api_key, encryption_key)
        config["API"] = {"encrypted_key": encrypted_api_key}
        with open("config.ini", "w") as configfile:
            config.write(configfile)
    else:
        config.read("config.ini")

    encrypted_api_key = config["API"]["encrypted_key"]
    encryption_key = keyring.get_password("gemini_chat",
                                          "encryption_key").encode()
    return decrypt_api_key(encrypted_api_key, encryption_key)


api_key = get_api_key()
genai.configure(api_key=api_key)

# --- File Handling Functions --- (Same as before, no changes)


def detect_file_in_prompt(prompt):
    match = re.search(r"([a-zA-Z]:[\\/](?:[\w\s\-\._()]+[\\/]?)+)", prompt)
    if match:
        file_path = match.group(0)
        absolute_path = os.path.abspath(file_path)

        if os.path.exists(absolute_path):
            return absolute_path
        else:
            rprint(
                f"[bold yellow]Detected path '{file_path}' (absolute: '{absolute_path}') does not exist.[/bold yellow]"
            )
            return None
    else:
        return None


def summarize_text(text, max_length=5000, chat_session=None):
    if len(text) <= max_length:
        return text
    summary_prompt = f"Summarize the following text:\n{text[:max_length]}..."
    return generate_response(chat_session, summary_prompt)


def extract_from_json(file_path, ai_suggestion=None):
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            data = json.load(file)
            if isinstance(data, dict) and ai_suggestion:
                keys_to_extract = [
                    key.strip() for key in ai_suggestion.split(",")
                ]
                return {key: data.get(key, "N/A") for key in keys_to_extract}
            return json.dumps(data, indent=4)
    except Exception as e:
        return f"[Error reading JSON: {str(e)}]"


def extract_from_excel(file_path, ai_suggestion=None):
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


def read_file_content(file_path, ai_suggestion=None, chat_session=None):
    if not os.path.exists(file_path):
        return "[Error] File not found."

    _, ext = os.path.splitext(file_path)
    try:
        if ext in [".txt", ".py", ".cs", ".js", ".xml"]:
            with open(file_path, "r", encoding="utf-8") as file:
                return summarize_text(file.read(), chat_session=chat_session)
        elif ext == ".json":
            return extract_from_json(file_path, ai_suggestion)
        elif ext in [".xlsx", ".xls"]:
            return extract_from_excel(file_path, ai_suggestion)
        elif ext == ".csv":
            df = pd.read_csv(file_path)
            return df.head().to_string(index=False)
        elif ext == ".log":
            with open(file_path, "r", encoding="utf-8") as file:
                lines = file.readlines()
                errors = [
                    line for line in lines if "ERROR" in line or "WARN" in line
                ]
                return ("\n".join(errors[:10])
                        if errors else "[No errors/warnings found]")
        elif ext == ".pdf":
            reader = PdfReader(file_path)
            content = "\n".join(page.extract_text() for page in reader.pages
                                if page.extract_text())
            return summarize_text(content, chat_session=chat_session)
        elif ext == ".docx":
            doc = docx.Document(file_path)
            full_text = []
            for paragraph in doc.paragraphs:
                full_text.append(paragraph.text)
            return summarize_text("\n".join(full_text),
                                  chat_session=chat_session)
        elif ext == ".pptx":
            prs = Presentation(file_path)
            full_text = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        full_text.append(shape.text)
            return summarize_text("\n".join(full_text),
                                  chat_session=chat_session)
        else:
            with open(file_path, "rb") as file:
                content = file.read()
                try:
                    text = content.decode("utf-8")
                    return summarize_text("\n".join(full_text),
                                          chat_session=chat_session)
                except:
                    return "Unsupported file type"
            return f"[Unsupported file type: {ext}]"
    except Exception as e:
        return f"[Error reading file: {str(e)}]"


# --- Gemini Interaction --- (Same as before, no changes)


def generate_response(chat_session,
                      prompt,
                      model_name="gemini-2.0-flash",
                      max_retries=5,
                      retry_delay=5):
    for attempt in range(max_retries):
        try:
            response = chat_session.send_message(prompt)
            return response.text
        except google_exceptions.ResourceExhausted:
            rprint(
                f"[bold yellow]Rate limit exceeded (attempt {attempt + 1}/{max_retries}). Waiting {retry_delay} seconds...[/bold yellow]"
            )
            time.sleep(retry_delay)
            retry_delay *= 2
        except google_exceptions.GoogleAPIError as e:
            rprint(f"[bold red]A Google API error occurred: {e}[/bold red]")
            return f"[Error] Google API error: {e}"
        except Exception as e:
            rprint(f"[bold red]An error occurred: {e}[/bold red]")
            return f"[Error] An error occurred: {e}"
    rprint(
        f"[bold red]Max retries exceeded. Failed to generate response.[/bold red]"
    )
    return "[Error] Max retries exceeded"


def apply_diff(original_content, diff_text):
    diff = difflib.unified_diff(original_content.splitlines(keepends=True),
                                 [line + "\n" for line in diff_text.splitlines()],
                                 fromfile="original",
                                 tofile="modified")

    return "".join(diff)


def generate_modification_proposal(chat_session, file_content, file_type,
                                   intent):
    if file_type in [".txt", ".cs", ".py", ".xml"]:
        prompt = (
            f"You are a file modification assistant. The user wants to {intent} in the following {file_type} file:\n\n"
            f"```\n{file_content}\n```\n\n"
            f"Generate a `diff` (using the standard `diff` format) that applies these changes to the file.\n"
            f"The `diff` should include `--- a/original_file` and `+++ b/modified_file` headers. Only generate a diff, don't add any other explanation."
        )
    elif file_type == ".json":
        prompt = (
            f"You are a file modification assistant. The user wants to {intent} in the following JSON file:\n\n"
            f"```json\n{file_content}\n```\n\n"
            f"Describe the changes that need to be made to the JSON file to fulfill the user's request. Return a valid JSONPatch"
            f"The output should be only a JSONPatch"
        )
    elif file_type == ".xlsx":
        prompt = (
            f"You are a file modification assistant. The user wants to {intent} in the following Excel file (represented as text):\n\n"
            f"{file_content}\n\n"
            f"Describe the changes that need to be made to the Excel file to fulfill the user's request. Focus on specific cells/rows/columns."
        )
    else:
        return "Unsupported file type for modification."

    return generate_response(chat_session, prompt)


# --- File System Operations --- (Modified to include delete_file)
def perform_file_operation(action, file_path, description, prompt, chat_session, content=None):
    """
    Dynamically generates and executes code for a file operation.
    """
    try:
        with open("data.json", "r") as f:
            data = json.load(f)
        valid_actions = {action["name"].lower(): action for action in data["actions"]}
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return f"[Error] Could not load action definitions: {e}"

    if action.lower() not in valid_actions:
        return f"[Error] Invalid action: {action}"

    action_data = valid_actions[action.lower()]
    prompt_template = action_data["prompt_template"]

    # --- Substitute values into the prompt template ---
    # repr() is all we need here!
    filled_prompt = prompt_template.format(file_path=repr(file_path), content=repr(content), description=repr(description))

    # --- Generate the code ---
    console.print(f"[bold magenta]Generating code for action: {action}[/bold magenta]")
    console.print(f"[bold magenta]Prompt sent to Gemini:\n{filled_prompt}[/bold magenta]")
    generated_code = generate_response(chat_session, filled_prompt)

    # Basic code validation (VERY IMPORTANT)
    if "def " not in generated_code:
        return "[Error] Generated code does not contain a function definition."
    if "```" in generated_code:  #Often Gemini returns codeblock
        generated_code = generated_code.split("```")[1]
        if generated_code.startswith('python'):
            generated_code = generated_code[6:]

    # Remove example usage (if __name__ == '__main__':)
    if_main_index = generated_code.find("if __name__ == '__main__':")
    if if_main_index != -1:
        generated_code = generated_code[:if_main_index]


    console.print(f"[bold magenta]Generated code:\n{generated_code}[/bold magenta]")

    # --- *Safely* execute the generated code ---
    local_vars = {}  # Restricted environment
    global_vars = {"os": os, "shutil": shutil}  # Provide access to os and shutil
    try:
        exec(generated_code, global_vars, local_vars)

        func_name = action.lower()  # Function naming convention
        if func_name in local_vars and callable(local_vars[func_name]):
            # Determine function arguments based on action (IMPORTANT)
            if action.lower() in ("read_file", "list_files"): # Add more read-like actions
                result = local_vars[func_name](file_path)
                return result  # Return content directly
            elif action.lower() in ("create_text_file", "append_to_file"): # Add more write-like actions
                result = local_vars[func_name](file_path, content)
                return result
            # Add handling for actions without arguments here, if needed
            elif action.lower() in ("delete_file",):
                result = local_vars[func_name](file_path)
                return result
            # Add more with 2 args such as copy, move, rename
            elif action.lower() in ("copy_file", "move_file"):
                dest_match = re.search(r"to\s+['\"]?((?:[a-zA-Z]:)?[\\/][^'\"]+)['\"]?", prompt, re.IGNORECASE)
                if not dest_match:
                    return "[Error] Missing destination path."
                destination_path = dest_match.group(1)
                result = local_vars[func_name](file_path, destination_path)
                return result
            elif action.lower() == "rename_file":
                new_name_match = re.search(r"to\s+['\"]?([^'\"]+)['\"]?", prompt, re.IGNORECASE)
                if not new_name_match:
                    return "[Error] Missing new name."
                new_name = new_name_match.group(1)
                result = local_vars[func_name](file_path, new_name)
                return result
            elif action.lower() == "backup_file":
                 result = local_vars[func_name](file_path)
                 return result
            elif action.lower() == "restore_file":
                backup_match = re.search(r"from\s+['\"]?((?:[a-zA-Z]:)?[\\/][^'\"]+)['\"]?", prompt, re.IGNORECASE)
                if not backup_match:
                    return "[Error] Missing backup file path."
                backup_path = backup_match.group(1)
                result = local_vars[func_name](file_path, backup_path)
                return result
            else:
                return f"[Error] Function call convention not defined for action: {action}."

        else:
            return f"[Error] Generated code did not define a callable function named '{func_name}'."

    except Exception as e:
        return f"[Error] Error executing generated code: {e}\nGenerated Code:\n{generated_code}"

def escape_file_path(file_path):
    """Ensures backslashes in file paths are properly escaped."""
    if file_path is None: #Handle None case
        return None
    return file_path.replace("\\", "\\\\")
    
def create_backup(file_path):
    backup_dir = os.path.join(os.path.dirname(file_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file_name = f"{os.path.basename(file_path)}_{timestamp}.bak"
    backup_file_path = os.path.join(backup_dir, backup_file_name)

    try:
        shutil.copy2(file_path, backup_file_path)
        return backup_file_path
    except Exception as e:
        print(f"Error creating backup: {e}")
        return None

def restore_backup(file_path, backup_file_path):
    try:
        shutil.copy2(backup_file_path, file_path)
        print(f"File restored from backup: {backup_file_path}")
    except Exception as e:
        print(f"Error restoring backup: {e}")


def delete_file(file_path):
    """Deletes a file, handling potential errors."""
    try:
        os.remove(file_path)
        return True, "[Success] File deleted successfully."
    except FileNotFoundError:
        return False, "[Error] File not found."
    except PermissionError:
        return False, "[Error] Permission denied.  You may not have the necessary rights to delete this file."
    except OSError as e:
        return False, f"[Error] An OS error occurred: {e}"
    except Exception as e:
        return False, f"[Error] An unexpected error occurred: {e}"

def generate_code(chat_session, prompt, file_path=None):
    response = generate_response(chat_session, prompt)

    if file_path:
        try:
            code_match = re.search(r"```python\n(.*?)\n```", response, re.DOTALL)
            if code_match:
                code = code_match.group(1)
            else:
                code = response

            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)
            return f"[Success] Code generated and saved to {file_path}"
        except Exception as e:
            return f"[Error] Failed to save code: {e}"
    else:
        return response


# --- Prompt Analysis and Processing --- (Significantly Modified)


def analyze_prompt(chat_session, prompt):
    try:
        with open("data.json", "r") as f:
            data = json.load(f)
        action_words = ", ".join([action["name"] for action in data["actions"]])
        analysis_prompt = (
            f"Analyze the following user prompt and break it down into a series of numbered steps. "
            f"Each step MUST begin with one of the following action words: {action_words}.\n"
            f"Format each step as: 'Number. [ACTION]: [Description]'\n"
            f"For example: '1. CREATE: a Python script named my_script.py'\n"
            f"If the needed action can't be found, please extract any action needed first."
            f"Prompt: {prompt}"
        )
        return generate_response(chat_session, analysis_prompt)
    except FileNotFoundError:
        return "[Error] data.json not found."
    except json.JSONDecodeError:
        return "[Error] Invalid JSON in data.json."


def process_prompt_with_file(chat_session, prompt):
    """Processes prompts, handling direct file creation and step-by-step actions."""
    console.print("[bold blue]Analyzing Prompt...[/bold blue]")

    try:
        with open("data.json", "r") as f:
            data = json.load(f)
        valid_actions = {action["name"].lower(): action for action in data["actions"]}
        action_keywords = [action["name"].lower() for action in data["actions"]] + ["file", "script", "path"]
    except FileNotFoundError:
        return "[Error] data.json not found."
    except json.JSONDecodeError:
        return "[Error] Invalid JSON in data.json."

    # Check if prompt is actionable
    prompt_lower = prompt.lower()
    is_actionable = any(keyword in prompt_lower for keyword in action_keywords)

    if not is_actionable:
        response_prompt = f"Respond naturally to this user request: '{prompt}'"
        response = generate_response(chat_session, response_prompt)
        display_formatted_response(response)
        return response

    # Direct file creation (Corrected Path Handling)
    file_path_match = re.search(r"(?<!['\"])((?:[a-zA-Z]:)?[\\/][^'\"]+)(?!['\"])", prompt)
    file_path = file_path_match.group(1) if file_path_match else None
    # NO escape_file_path here.  Let os.path.join and repr() handle it.
    content_match = re.search(r"content\s*['\"]([^'\"]+)['\"]", prompt, re.IGNORECASE)
    content = content_match.group(1) if content_match else None

    if file_path and content:
        console.print("[bold blue]Detected direct file creation request. Processing...[/bold blue]")
        result = perform_file_operation("CREATE_TEXT_FILE", file_path, "Create file with content", prompt, chat_session, content)
        display_formatted_response(result)
        return result

    # Step-by-step processing (Corrected Path Handling)
    console.print("[bold blue]Detected potential file operation or action. Processing steps...[/bold blue]")
    analysis = analyze_prompt(chat_session, prompt)
    console.print(f"[bold blue]Prompt Analysis:[/bold blue]\n{analysis}")

    steps = []
    for line in analysis.split("\n"):
        match = re.match(r"(\d+)\.\s*(\w+):\s*(.+)", line.strip())
        if match:
            step_num, action, description = match.groups()
            steps.append({"number": int(step_num), "action": action.lower(), "description": description})

    if steps and all(step["action"] not in valid_actions for step in steps):
        return "[Error] No actionable steps found in analysis."

    results = []
    for step in steps:
        action = step["action"]
        description = step["description"]

        file_path_match = re.search(r"(?<!['\"])((?:[a-zA-Z]:)?[\\/][^'\"]+)(?!['\"])", description)
        file_path = file_path_match.group(1) if file_path_match else None
        # NO escape_file_path here

        if not file_path:
            dir_match = re.search(r"in\s+['\"]?((?:[a-zA-Z]:)?[\\/][^'\"]+)['\"]?", prompt_lower)
            file_match = re.search(r"['\"]([^'\"]+\.\w+)['\"]", description)
            if dir_match and file_match:
                # Use os.path.join *consistently*
                file_path = os.path.join(dir_match.group(1), file_match.group(1))
                # NO escape_file_path here

        content_match = re.search(r"content\s*['\"]([^'\"]+)['\"]", description + prompt, re.IGNORECASE)
        content = content_match.group(1) if content_match else None

        # Call the perform_file_operation function
        result = perform_file_operation(action, file_path, description, prompt, chat_session, content)
        results.append(result)

    final_response = "\n".join(results)
    if "[Error]" in final_response:
        console.print("[bold red]An error occurred. See details above.[/bold red]")
    elif "[Success]" in final_response or "[Result]" in final_response:
        console.print("[bold green]Task completed. Results:[/bold green]")
    else:
        console.print("[bold yellow]No action taken.[/bold yellow]")

    display_formatted_response(final_response)
    return final_response


def analyze_prompt(chat_session, prompt):
    try:
        with open("data.json", "r") as f:
            data = json.load(f)
        action_words = ", ".join([action["name"] for action in data["actions"]])
        analysis_prompt = (
            f"Break down the user prompt into numbered steps, each starting with an action word from this list: {action_words}.\n"
            f"Format: 'Number. ACTION: Description'.  If creating a file AND adding content, combine into a single step.\n"
            f"Prompt: {prompt}"
        )
        return generate_response(chat_session, analysis_prompt)
    except FileNotFoundError:
        return "[Error] data.json not found."
    except json.JSONDecodeError:
        return "[Error] Invalid JSON in data.json."

# --- Utility Functions --- (Same as before, no changes)

def display_formatted_response(response_text):
    global all_code_blocks
    segments = response_text.split("```")
    for i, segment in enumerate(segments):
        if i % 2 == 0:
            console.print(Markdown(segment.strip()))
        else:
            lines = segment.split("\n")
            language = lines[0].strip() if lines[0].strip() else "python"
            code = "\n".join(lines[1:])
            syntax = Syntax(code, language, theme="monokai", line_numbers=True)
            console.print(
                Panel(
                    syntax,
                    title=f"[bold]{language.capitalize()} Code[/bold]",
                    expand=True,
                ))
            all_code_blocks.append(code)


def get_user_prompt():
    try:
        prompt = console.input(
            "[bold green]Enter your prompt (or 'quit'/'exit' to terminate): [/bold green]"
        )
        if prompt.lower() in ["quit", "exit"]:
            quit_application()
        return prompt
    except KeyboardInterrupt:
        quit_application()


def copy_to_clipboard():
    global all_code_blocks
    if all_code_blocks:
        all_code = "\n\n".join(all_code_blocks)
        pyperclip.copy(all_code)
        rprint(
            "\n[bold green]All code blocks copied to clipboard![/bold green]")
    else:
        rprint("\n[bold yellow]No code available to copy.[/bold yellow]")


def quit_application(signum=None, frame=None):
    rprint("\n[bold red]Exiting the program. Goodbye![/bold red]")
    keyboard.unhook_all()
    exit(0)


# --- Main Function --- (No changes)


def main():
    parser = argparse.ArgumentParser(
        description="Enhanced Gemini Chat with File Processing")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    chat_parser = subparsers.add_parser("chat",
                                        help="Start interactive chat mode")

    args = parser.parse_args()

    keyboard.add_hotkey("ctrl+alt+c", copy_to_clipboard)

    if args.command == "chat" or args.command is None:
        rprint("[bold]Welcome to the Enhanced Gemini Chat![/bold]")
        rprint("[bold cyan]Press Ctrl+Alt+C to copy code blocks.[/bold cyan]")
        rprint("[bold cyan]Press Ctrl+C or type 'quit' to exit.[/bold cyan]")

        model = genai.GenerativeModel("gemini-2.0-flash")
        chat_session = model.start_chat()

        while True:
            try:
                prompt = get_user_prompt()
                if not prompt:
                    continue

                # --- Early Check for Non-Actionable Prompts ---
                try:
                    with open("data.json", "r") as f:
                        data = json.load(f)
                    action_keywords = [action["name"].lower() for action in data["actions"]] + ["file", "script", "path"]
                except (FileNotFoundError, json.JSONDecodeError):
                    console.print("[bold red]Error loading data.json.  Action detection may not work correctly.[/bold red]")
                    action_keywords = ["file", "script", "path"] # Fallback

                prompt_lower = prompt.lower()
                is_actionable = any(keyword in prompt_lower for keyword in action_keywords)

                if not is_actionable:
                    # console.print("[bold blue]Detected non-actionable prompt. Generating natural response...[/bold blue]")
                    response = generate_response(chat_session, prompt)  # Use the basic generate_response
                    display_formatted_response(response)
                    continue  # Skip process_prompt_with_file


                rprint("[bold yellow]Generating response...[/bold yellow]")
                response = process_prompt_with_file(chat_session, prompt)
                display_formatted_response(response)

            except Exception as e:
                console.print(
                    f"[bold red]An error occurred: {str(e)}[/bold red]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        quit_application()

