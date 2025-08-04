import os
import platform
import subprocess
import tempfile
import logging
import mimetypes
import base64
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class DocumentPrinter:
    """
    Cross-platform document printer supporting text and all file types.
    Features:
    - Print text content directly
    - Print any file type (PDF, DOC, TXT, images, etc.)
    - Automatic file type detection
    - PDF fallback for failed print jobs
    - Cross-platform support (Windows, macOS, Linux)
    """

    def __init__(self):
        self.system = platform.system()
        self.downloads_path = Path.home() / "Downloads"
        
        # Supported file types for direct printing
        self.printable_types = {
            '.txt', '.pdf', '.doc', '.docx', '.rtf', '.odt',
            '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff',
            '.html', '.htm', '.xml', '.csv', '.json'
        }
        
        # Initialize mimetypes
        mimetypes.init()

    def _detect_file_type(self, file_path: Path) -> tuple:
        """
        Detect file type and return (mime_type, is_text, is_printable)
        """
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if not mime_type:
            mime_type = 'application/octet-stream'
        
        is_text = mime_type.startswith('text/') or file_path.suffix.lower() in {'.txt', '.csv', '.json', '.xml', '.html', '.htm'}
        is_printable = file_path.suffix.lower() in self.printable_types
        
        return mime_type, is_text, is_printable
    
    def _read_file_content(self, file_path: Path) -> str:
        """
        Read file content as text. For binary files, return a description.
        """
        mime_type, is_text, _ = self._detect_file_type(file_path)
        
        if is_text:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except UnicodeDecodeError:
                try:
                    with open(file_path, 'r', encoding='latin-1') as f:
                        return f.read()
                except Exception:
                    return f"[Binary file: {file_path.name}]\nMIME Type: {mime_type}\nSize: {file_path.stat().st_size} bytes"
        else:
            # For binary files, create a text representation
            size = file_path.stat().st_size
            return f"""File Information:
Name: {file_path.name}
Type: {mime_type}
Size: {size:,} bytes
Path: {file_path}

[This is a binary file that cannot be displayed as text]
[Original file will be sent to printer if supported]"""

    def _write_temp_text(self, content) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        f.write(str(content))
        f.flush()
        f.close()
        return Path(f.name)
    
    def _prepare_file_for_printing(self, content_or_path) -> tuple:
        """
        Prepare content for printing. Returns (file_path, is_temp_file, original_content)
        """
        if isinstance(content_or_path, (str, Path)) and Path(content_or_path).exists():
            # It's an existing file
            file_path = Path(content_or_path)
            original_content = self._read_file_content(file_path)
            return file_path, False, original_content
        else:
            # It's text content - create temp file
            file_path = self._write_temp_text(content_or_path)
            return file_path, True, str(content_or_path)

    def _write_minimal_pdf(self, content, output_path: Path) -> bool:
        """
        Very naive PDF writer: places text in a single page using default fonts.
        Not full-featured; works for basic ASCII lines. 
        """
        try:
            lines = str(content).splitlines()
            # PDF objects
            objs = []
            xref_offsets = []

            def add_obj(s):
                xref_offsets.append(len(b''.join(objs)))
                objs.append(s)
                return len(xref_offsets)  # object number

            # Catalog
            # Prepare content stream: simple text using BT/ET
            text_lines = []
            text_lines.append("BT /F1 12 Tf 50 750 Td")
            for line in lines:
                safe = line.replace("(", "\\(").replace(")", "\\)")
                text_lines.append(f"({safe}) Tj 0 -14 Td")
            text_stream = "\n".join(text_lines)
            stream = f"""q
1 0 0 1 0 0 cm
BT
/F1 12 Tf
50 750 Td
{text_stream}
ET
Q
"""
            # Create font object
            font_obj_num = add_obj(f"""<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>""".encode("utf-8"))
            # Content stream object
            content_stream_bytes = stream.encode("utf-8")
            content_obj_num = add_obj(
                f"""<< /Length {len(content_stream_bytes)} >>\nstream\n{stream}\nendstream""".encode("utf-8")
            )
            # Page object
            page_obj_num = add_obj(
                f"""<< /Type /Page /Parent 4 0 R /Resources << /Font << /F1 {font_obj_num} 0 R >> >> /Contents {content_obj_num} 0 R /MediaBox [0 0 612 792] >>""".encode("utf-8")
            )
            # Pages root
            pages_obj_num = add_obj(
                f"""<< /Type /Pages /Kids [ {page_obj_num} 0 R ] /Count 1 >>""".encode("utf-8")
            )
            # Catalog
            catalog_obj_num = add_obj(f"""<< /Type /Catalog /Pages {pages_obj_num} 0 R >>""".encode("utf-8"))

            # Build PDF binary
            pdf = b"%PDF-1.4\n"
            # write objects with numbering
            for idx, obj in enumerate(objs, start=1):
                xref_offsets[idx - 1] = len(pdf)
                pdf += f"{idx} 0 obj\n".encode("utf-8")
                if isinstance(obj, bytes):
                    pdf += obj
                else:
                    pdf += obj.encode("utf-8")
                pdf += b"\nendobj\n"
            # xref
            xref_start = len(pdf)
            pdf += b"xref\n"
            pdf += f"0 {len(objs)+1}\n".encode("utf-8")
            pdf += b"0000000000 65535 f \n"
            for offset in xref_offsets:
                pdf += f"{offset:010d} 00000 n \n".encode("utf-8")
            # trailer
            pdf += b"trailer\n"
            pdf += f"""<< /Size {len(objs)+1} /Root {catalog_obj_num} 0 R >>\n""".encode("utf-8")
            pdf += b"startxref\n"
            pdf += f"{xref_start}\n".encode("utf-8")
            pdf += b"%%EOF\n"

            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(pdf)
            logger.info(f"Minimal PDF written to {output_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to write minimal PDF: {e}")
            return False

    def _fallback_pdf_save(self, content, filename=None):
        if not filename:
            filename = f"document_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        elif not filename.lower().endswith(".pdf"):
            filename += ".pdf"
        pdf_path = self.downloads_path / filename
        success = self._write_minimal_pdf(content, pdf_path)
        if success:
            return pdf_path
        # Last-resort: plain text with .pdf extension (warn)
        try:
            with open(pdf_path, "w", encoding="utf-8") as f:
                f.write("<< WARNING: Could not build PDF, fallback to text >>\n")
                f.write(str(content))
            logger.info(f"Fallback text-as-.pdf written to {pdf_path}")
            return pdf_path
        except Exception as e:
            logger.error(f"Final fallback write failed: {e}")
            return None

    def _print_unix(self, file_path: Path, printer_name=None, to_pdf_path: Path = None):
        cmd = ["lp"]
        if printer_name:
            cmd += ["-d", printer_name]
        if to_pdf_path:
            # CUPS print-to-file (PDF) if supported
            cmd += ["-o", f"outputfile={to_pdf_path}"]
        cmd.append(str(file_path))
        return subprocess.run(cmd, capture_output=True, check=True)

    def _print_windows(self, file_path: Path, printer_name=None):
        # If the user wants Microsoft Print to PDF, the system normally pops up a dialog.
        if printer_name and "Microsoft Print to PDF" in printer_name:
            # Use ShellExecute print verb
            subprocess.run(
                ["rundll32.exe", "shell32.dll,ShellExec_RunDLL", str(file_path), "print"],
                check=True,
            )
            return
        # Generic print via shell verb
        try:
            subprocess.run(
                ["rundll32.exe", "shell32.dll,ShellExec_RunDLL", str(file_path), "print"],
                check=True,
            )
        except subprocess.CalledProcessError:
            # Fallback to notepad for .txt
            if file_path.suffix.lower() == ".txt":
                cmd = f'notepad /P "{file_path}"'
                subprocess.run(cmd, shell=True, check=True)
            else:
                raise

    def print_document(self, content_or_path, printer_name=None, fallback_to_pdf=True, pdf_filename=None):
        """
        Print text content or any file type with PDF fallback support.
        
        Args:
            content_or_path: Text string or path to file (any type)
            printer_name: Optional printer name or "PDF" for print-to-PDF
            fallback_to_pdf: Create PDF if printing fails (default: True)
            pdf_filename: Custom filename for PDF fallback
            
        Returns:
            tuple: (success: bool, message: str, pdf_path_or_None: str)
        """
        temp_file = None
        try:
            # Prepare file for printing
            file_path, is_temp, original_content = self._prepare_file_for_printing(content_or_path)
            temp_file = file_path if is_temp else None
            
            # Detect file type for better handling
            mime_type, is_text, is_printable = self._detect_file_type(file_path)
            
            logger.info(f"Printing file: {file_path.name} (Type: {mime_type})")
            
            # Attempt to print based on OS
            if self.system in ("Darwin", "Linux"):
                try:
                    # If printer_name indicates PDF, interpret as print-to-PDF
                    to_pdf = None
                    if printer_name and "pdf" in printer_name.lower():
                        default_pdf_name = pdf_filename or f"print_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                        to_pdf = self.downloads_path / default_pdf_name
                    
                    self._print_unix(file_path, printer_name=printer_name, to_pdf_path=to_pdf)
                    
                    if to_pdf:
                        return True, f"Printed to PDF: {to_pdf}", str(to_pdf)
                    else:
                        return True, f"Printed successfully via lp. File type: {mime_type}", None
                        
                except subprocess.CalledProcessError as e:
                    logger.warning(f"Unix print failed: {e}; stderr: {getattr(e, 'stderr', None)}")
                    raise

            elif self.system == "Windows":
                try:
                    self._print_windows(file_path, printer_name=printer_name)
                    return True, f"Printed successfully on Windows. File type: {mime_type}", None
                except subprocess.CalledProcessError as e:
                    logger.warning(f"Windows print failed: {e}")
                    raise

            else:
                return False, f"Unsupported OS: {self.system}", None

        except Exception as e:
            logger.error(f"Printing error: {e}")
            if fallback_to_pdf:
                # Use original content for PDF fallback
                content_for_pdf = original_content if 'original_content' in locals() else str(content_or_path)
                pdf_path = self._fallback_pdf_save(content_for_pdf, pdf_filename)
                if pdf_path:
                    return False, f"Printing failed. PDF fallback saved to: {pdf_path}", str(pdf_path)
                else:
                    return False, "Printing failed. PDF fallback also failed.", None
            else:
                return False, "Printing failed and fallback disabled.", None
        finally:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    logger.debug("Could not delete temp file; ignoring.")
    
    def print_file(self, file_path, printer_name=None, fallback_to_pdf=True, pdf_filename=None):
        """
        Convenience method to print any file type.
        
        Args:
            file_path: Path to file (any type: PDF, DOC, TXT, images, etc.)
            printer_name: Optional printer name
            fallback_to_pdf: Create PDF if printing fails (default: True)
            pdf_filename: Custom filename for PDF fallback
            
        Returns:
            tuple: (success: bool, message: str, pdf_path_or_None: str)
        """
        if not Path(file_path).exists():
            return False, f"File not found: {file_path}", None
            
        return self.print_document(file_path, printer_name, fallback_to_pdf, pdf_filename)
    
    def print_text(self, text, printer_name=None, fallback_to_pdf=True, pdf_filename=None):
        """
        Convenience method to print text content.
        
        Args:
            text: Text string to print
            printer_name: Optional printer name
            fallback_to_pdf: Create PDF if printing fails (default: True)
            pdf_filename: Custom filename for PDF fallback
            
        Returns:
            tuple: (success: bool, message: str, pdf_path_or_None: str)
        """
        return self.print_document(str(text), printer_name, fallback_to_pdf, pdf_filename)
    
    def get_supported_file_types(self):
        """
        Get list of supported file types for direct printing.
        
        Returns:
            set: Set of supported file extensions
        """
        return self.printable_types.copy()
    
    def is_file_printable(self, file_path):
        """
        Check if a file type is directly printable.
        
        Args:
            file_path: Path to file
            
        Returns:
            bool: True if file type is supported for direct printing
        """
        return Path(file_path).suffix.lower() in self.printable_types
