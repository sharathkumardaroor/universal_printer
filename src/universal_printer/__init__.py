"""
Universal Printer - Cross-platform document printing with PDF fallback.

A dependency-free Python library that works across Windows, macOS, and Linux
to print text and documents, with a fallback to generating minimal PDFs using
only the standard library.
"""

from .document_printer import DocumentPrinter

__version__ = "2.0.0"
__author__ = "Sharath Kumar Daroor"
__email__ = "sharathkumardaroor@gmail.com"

__all__ = ["DocumentPrinter"]