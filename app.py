# DEVELOPMENT COPY - LICENSE CHECKS DISABLED
# This file preserves the application features and database logic.

from wrapt import decorator

from config import BACKUP_DIR


from decimal import Decimal, InvalidOperation

import json

import secrets

from pathlib import Path

from collections import defaultdict

from sqlalchemy import inspect, text, values

from sqlalchemy import or_, func, event

import logging

from logging.handlers import RotatingFileHandler

from schema_migration import run_schema_migration

from models import (
    db,
    User,
    Customer,
    Supplier,
    DirectStockPayment, 
    Product,
    Purchase,
    PurchaseItem,
    Sale,
    SaleItem,
    Payment,
    StockMovement,
    AuditLog,
    StoreSettings,
    Expense,
    IMEIRecord,
    UsedHandsetSeller,
    UsedSellerPayment,
    ServiceJob,
    ServicePart,
    ServiceStock,
    ServiceHistory,
)

from io import BytesIO
from flask import make_response, request
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from functools import wraps

from flask_migrate import Migrate

import base64
import webview
import os
import math
import threading
import time as time_module
import urllib.request
import uuid
from werkzeug.utils import secure_filename

from utils.database_backup import (
    create_database_backup,
    validate_database_backup,
)

import sqlite3

from datetime import date, datetime, time, timedelta

from functools import wraps
from xml.sax.saxutils import escape

from flask import (
    Flask,
    abort,
    request,
    render_template,
    render_template_string,
    redirect,
    url_for,
    flash,
    current_app,
    jsonify,
    send_from_directory,
    send_file,
)
from flask_login import (
    LoginManager,
    current_user,
    login_user,
    logout_user,
    login_required,
)
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import or_, func

from config import Config, DATA_DIR

login_manager = LoginManager()
csrf = CSRFProtect()

app = Flask(__name__)


ROLES = {"admin", "staff"}


def apply_date_filters(query, model, date_from="", date_to=""):
    """Apply optional start and end date filters to a SQLAlchemy query.

    Uses the first likely date-like column on the model, preferring explicit
    fields such as sale_date / purchase_date / expense_date before created_at.
    """
    def _normalize_date(value, *, as_end=False):
        if not value:
            return None

        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"Invalid date: {value}") from exc

        if as_end:
            return datetime.combine(parsed, time.max)
        return datetime.combine(parsed, time.min)

    date_columns = [
        "sale_date",
        "purchase_date",
        "expense_date",
        "created_at",
        "updated_at",
        "date",
    ]

    if date_from:
        start_dt = _normalize_date(date_from)
        column = None
        for candidate in date_columns:
            if hasattr(model, candidate):
                column = getattr(model, candidate)
                break

        if column is None:
            raise ValueError("No date column available for filtering.")

        query = query.filter(column >= start_dt)

    if date_to:
        end_dt = _normalize_date(date_to, as_end=True)
        column = None
        for candidate in date_columns:
            if hasattr(model, candidate):
                column = getattr(model, candidate)
                break

        if column is None:
            raise ValueError("No date column available for filtering.")

        query = query.filter(column <= end_dt)

    return query


def roles_required(*allowed_roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in allowed_roles:
                flash(
                    "You do not have permission to access this page.",
                    "danger",
                )
                return redirect(url_for("dashboard_page"))

            return view(*args, **kwargs)

        return wrapped

    return decorator



def save_private_document(upload, folder="used_handsets"):
    if not upload or not upload.filename:
        return None

    extension = os.path.splitext(upload.filename)[1].lower()

    if extension not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("Only JPG, JPEG and PNG files are allowed.")

    file_data = upload.read(2 * 1024 * 1024 + 1)

    if not file_data:
        raise ValueError("The uploaded document is empty.")

    if len(file_data) > 2 * 1024 * 1024:
        raise ValueError("Document size must not exceed 2 MB.")

    is_jpeg = file_data.startswith(b"\xff\xd8\xff")
    is_png = file_data.startswith(b"\x89PNG\r\n\x1a\n")

    if extension in {".jpg", ".jpeg"} and not is_jpeg:
        raise ValueError("Invalid JPG image.")

    if extension == ".png" and not is_png:
        raise ValueError("Invalid PNG image.")

    storage_dir = (
        current_app.config["PRIVATE_DOCUMENT_DIR"] / folder
    )

    storage_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{secrets.token_hex(16)}{extension}"
    file_path = storage_dir / filename

    with open(file_path, "wb") as document:
        document.write(file_data)

    return f"{folder}/{filename}"

# --------------------------------------------------
# APPLICATION
# --------------------------------------------------

def create_app():
    app.config.from_object(Config)

    from config import DATABASE_FILE, BACKUP_DIR

    print("DATABASE FILE:", Config.SQLALCHEMY_DATABASE_URI)
    print("BACKUP DIRECTORY:", BACKUP_DIR)

        # Private document storage
    app.config["PRIVATE_DOCUMENT_DIR"] = (
        DATA_DIR / "private_documents"
    )

    app.config["PRIVATE_DOCUMENT_DIR"].mkdir(
        parents=True,
        exist_ok=True,
    )

        # --------------------------------------------------
    # PERSISTENT APPLICATION LOGGING
    # --------------------------------------------------
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "app.log"

    if not any(
        getattr(handler, "_mobilestore_handler", False)
        for handler in app.logger.handlers
    ):
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )

        file_handler._mobilestore_handler = True

        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))

        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)

    app.logger.setLevel(logging.INFO)
    app.logger.info("MobileStore ERP application initialized.")
    
     # Store logo uploads
    app.config["UPLOAD_FOLDER"] = (
    str(Config.UPLOAD_FOLDER)
    if Config.UPLOAD_FOLDER
    else os.path.join(app.instance_path, "store_logos")
    )

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    app.config["ALLOWED_LOGO_EXTENSIONS"] = {
        "png", "jpg", "jpeg", "webp"
    }
    app.config["MAX_LOGO_SIZE"] = 2 * 1024 * 1024  # 2 MB


    db.init_app(app)

    # SQLite connection settings and application initialization.
    from sqlalchemy import event

    with app.app_context():
        @event.listens_for(db.engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")
            finally:
                cursor.close()

        # db.create_all()
        run_schema_migration(app)
        login_manager.init_app(app)
        csrf.init_app(app)

        # app.add_url_rule(
        #     "/inventory/direct-stock",
        #     view_func=direct_stock_register,
        #     methods=["GET", "POST"],
        # )

        login_manager.login_view = "login_page"
        login_manager.login_message = "Please log in to continue."
        login_manager.login_message_category = "warning"

    
    # --------------------------------------------------
    # ROLE AUTHORIZATION
    # --------------------------------------------------
    
    
    # --------------------------------------------------
    # AUDIT LOGS
    # --------------------------------------------------
    @app.route("/audit-logs")
    @login_required
    @roles_required("admin")
    def audit_logs():
        logs = (
            AuditLog.query
            .order_by(AuditLog.created_at.desc())
            .limit(500)
            .all()
        )

        return render_template(
            "audit_logs.html",
            logs=logs
        )

    
    @app.route("/admin/database-management")
    @login_required
    @roles_required("admin")
    def database_management():

        backup_history = []

        backup_categories = [
            "manual",
            "before_migration",
            "before_truncate",
            "before_restore",
        ]

        for category in backup_categories:
            category_dir = Path(BACKUP_DIR) / category

            if not category_dir.exists():
                continue

            for backup_file in category_dir.glob("*.db"):
                backup_history.append({
                    "name": backup_file.name,
                    "category": category,
                    "date": datetime.fromtimestamp(
                        backup_file.stat().st_mtime
                    ),
                    "size": round(
                        backup_file.stat().st_size / (1024 * 1024),
                        2
                    ),
                })

        backup_history.sort(
            key=lambda item: item["date"],
            reverse=True
        )

        return render_template(
            "database_management.html",
            backup_history=backup_history
        )

    
    @app.route("/admin/database-management/backup", methods=["POST"])
    @login_required
    @roles_required("admin")
    def database_create_backup():
        try:
            backup_path = create_database_backup(
            category="manual"
            )

            flash(
            f"Database backup created successfully: {backup_path.name}",
            "success"
            )

        except Exception as e:
            current_app.logger.exception(
            "Database backup failed"
            )

            flash(
            f"Database backup failed: {str(e)}",
            "danger"
            )

        return redirect(url_for("database_management"))
    
    
    @app.route("/admin/database-management/restore", methods=["POST"])
    @login_required
    @roles_required("admin")
    def database_restore():
        confirmation = request.form.get("confirmation", "").strip()

        if confirmation != "RESTORE":
            flash(
                "Confirmation text is incorrect. No restoration was performed.",
                "danger"
            )
            return redirect(url_for("database_management"))

        backup_categories = [
            "manual",
            "before_migration",
            "before_truncate",
            "before_restore",
        ]

        source_path = None
        uploaded_path = None
        current_backup = None
        restore_completed = False

        try:
            # --------------------------------------------------
            # 1. Get uploaded backup or select an existing backup
            # --------------------------------------------------

            uploaded_file = request.files.get("backup_file")

            if uploaded_file and uploaded_file.filename:

                if Path(uploaded_file.filename).suffix.lower() != ".db":
                    raise ValueError(
                        "Please upload a valid .db file."
                    )

                if (
                    request.content_length
                    and request.content_length > 500 * 1024 * 1024
                ):
                    raise ValueError(
                        "Uploaded backup exceeds the 500 MB limit."
                    )

                upload_dir = Path(BACKUP_DIR) / "restore_uploads"
                upload_dir.mkdir(parents=True, exist_ok=True)

                uploaded_path = upload_dir / (
                    f"restore_upload_{uuid.uuid4().hex}.db"
                )

                uploaded_file.save(uploaded_path)
                source_path = uploaded_path

            else:

                backup_category = request.form.get(
                    "backup_category", ""
                )

                backup_name = request.form.get(
                    "backup_name", ""
                )

                if backup_category not in backup_categories:
                    raise ValueError(
                        "Invalid backup category."
                    )

                if (
                    not backup_name
                    or Path(backup_name).name != backup_name
                    or Path(backup_name).suffix.lower() != ".db"
                ):
                    raise ValueError(
                        "Invalid backup selection."
                    )

                category_dir = (
                    Path(BACKUP_DIR) / backup_category
                ).resolve()

                source_path = (
                    category_dir / backup_name
                ).resolve()

                if source_path.parent != category_dir:
                    raise ValueError(
                        "Invalid backup location."
                    )

            if not source_path or not source_path.is_file():
                raise ValueError(
                    "Selected backup file was not found."
                )

            # --------------------------------------------------
            # 2. Identify the active database
            # --------------------------------------------------

            target_path = Path(
                db.engine.url.database
            ).resolve()

            if source_path.resolve() == target_path:
                raise ValueError(
                    "You cannot restore the active database file."
                )

            # --------------------------------------------------
            # 3. Validate the selected backup
            # --------------------------------------------------

            is_valid, validation_message = validate_database_backup(
                source_path
            )

            if not is_valid:
                raise ValueError(validation_message)

            # --------------------------------------------------
            # 4. Check table and column compatibility
            # --------------------------------------------------

            source_conn = sqlite3.connect(
                str(source_path),
                timeout=30
            )

            target_conn = sqlite3.connect(
                str(target_path),
                timeout=30
            )

            try:

                source_tables = {
                    row[0]
                    for row in source_conn.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                        AND name NOT LIKE 'sqlite_%'
                        """
                    ).fetchall()
                }

                target_tables = {
                    row[0]
                    for row in target_conn.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                        AND name NOT LIKE 'sqlite_%'
                        """
                    ).fetchall()
                }

                missing_tables = target_tables - source_tables

                if missing_tables:
                    raise ValueError(
                        "Backup is missing application tables: "
                        + ", ".join(sorted(missing_tables))
                    )

                for table_name in target_tables:

                    source_columns = {
                        row[1]
                        for row in source_conn.execute(
                            f'PRAGMA table_info("{table_name}")'
                        ).fetchall()
                    }

                    target_columns = {
                        row[1]
                        for row in target_conn.execute(
                            f'PRAGMA table_info("{table_name}")'
                        ).fetchall()
                    }

                    missing_columns = (
                        target_columns - source_columns
                    )

                    if missing_columns:
                        raise ValueError(
                            f"Backup table '{table_name}' is missing columns: "
                            + ", ".join(sorted(missing_columns))
                        )

            finally:
                source_conn.close()
                target_conn.close()

            # --------------------------------------------------
            # 5. Create a backup of the current database
            # --------------------------------------------------

            current_backup = create_database_backup(
                category="before_restore"
            )

            # --------------------------------------------------
            # 6. Stop using existing SQLAlchemy connections
            # --------------------------------------------------

            db.session.remove()
            db.engine.dispose()

            # --------------------------------------------------
            # 7. Restore using SQLite backup API
            # --------------------------------------------------

            source_conn = sqlite3.connect(
                str(source_path),
                timeout=30
            )

            target_conn = sqlite3.connect(
                str(target_path),
                timeout=30
            )

            try:

                source_conn.backup(
                    target_conn,
                    pages=100,
                    sleep=0.25
                )

                # Verify restored database integrity.
                integrity = target_conn.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]

                if integrity != "ok":
                    raise RuntimeError(
                        "Restored database integrity check failed: "
                        + str(integrity)
                    )

                # Verify foreign-key consistency.
                foreign_key_errors = target_conn.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()

                if foreign_key_errors:
                    raise RuntimeError(
                        "Restored database contains foreign-key errors."
                    )

                restore_completed = True

            finally:
                target_conn.close()
                source_conn.close()

            # --------------------------------------------------
            # 8. Dispose engine for fresh future connections
            # --------------------------------------------------

            db.engine.dispose()

            flash(
                f"Database restored successfully from {source_path.name}. "
                f"Previous database backup: {current_backup.name}",
                "success"
            )

        except Exception as e:

            current_app.logger.exception(
                "Database restoration failed"
            )

            if current_backup and not restore_completed:
                flash(
                    "Database restoration failed. Your pre-restore backup "
                    f"was saved as {current_backup.name}. "
                    "Check the database before retrying.",
                    "danger"
                )
            else:
                flash(
                    f"Database restoration failed: {str(e)}",
                    "danger"
                )

        finally:

            if uploaded_path and uploaded_path.exists():
                try:
                    uploaded_path.unlink()
                except OSError:
                    current_app.logger.exception(
                        "Could not remove temporary restore upload."
                    )

        return redirect(url_for("database_management"))
    
    @app.route("/admin/database-management/truncate", methods=["POST"])
    @login_required
    @roles_required("admin")
    def database_truncate():
        confirmation = request.form.get("confirmation", "").strip()

        if confirmation != "TRUNCATE":
            flash(
                "Confirmation text is incorrect. No records were deleted.",
                "danger"
            )
            return redirect(url_for("database_management"))

        try:
            # Automatically back up the database before truncation.
            backup_path = create_database_backup(
                category="before_truncate"
            )

            # Delete dependent records before their parent records.
            delete_order = [
                "used_handset_sellers",
                "service_history",
                "service_parts",
                "imei_records",
                "purchase_items",
                "sale_items",
                "payments",
                "stock_movements",
                "service_jobs",
                "purchases",
                "sales",
                "expenses",
                "audit_logs",
                "customers",
                "suppliers",
                "products",
                "service_stock",
            ]

            with db.engine.begin() as connection:
                connection.exec_driver_sql(
                    "PRAGMA defer_foreign_keys = ON"
                )

                for table_name in delete_order:
                    connection.exec_driver_sql(
                        f'DELETE FROM "{table_name}"'
                    )

                # Reset auto-increment IDs for the cleared tables.
                sequence_table_exists = connection.exec_driver_sql(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    AND name = 'sqlite_sequence'
                    """
                ).fetchone()

                if sequence_table_exists:
                    connection.exec_driver_sql(
                        """
                        DELETE FROM sqlite_sequence
                        WHERE name IN (
                            'used_handset_sellers',
                            'service_history',
                            'service_parts',
                            'imei_records',
                            'purchase_items',
                            'sale_items',
                            'payments',
                            'stock_movements',
                            'service_jobs',
                            'purchases',
                            'sales',
                            'expenses',
                            'audit_logs',
                            'customers',
                            'suppliers',
                            'products',
                            'service_stock'
                        )
                        """
                    )

            flash(
                f"All business records were cleared successfully. "
                f"Backup created: {backup_path.name}",
                "success"
            )

        except Exception:
            current_app.logger.exception(
                "Database truncation failed"
            )

            flash(
                "Database truncation failed. The transaction was rolled back where possible. "
                "Check the application log and backup before retrying.",
                "danger"
            )

        return redirect(url_for("database_management"))


    # --------------------------------------------------
    # REPORT : PDF
    # --------------------------------------------------

    
    @app.route("/reports/pdf/<report_type>")
    @login_required
    @roles_required("admin")
    def report_pdf(report_type):
        allowed_reports = {
        "sales": "Sales Report",
        "inventory": "Inventory Report",
        "purchases": "Purchase Report",
        "customers": "Customer Report",
        "expenses": "Expense Report",
        "imei": "IMEI Report",

        # New handset reports
        "stock_ageing": "Handset Stock Ageing Report",
        "daily_handset_sales": "Daily Handset Sales Report",
        "monthly_handset_sales": "Monthly Handset Sales Report",
        "handset_summary": "Complete Handset Report",
        }

        if report_type not in allowed_reports:
            return "Invalid report type.", 404

        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()

        try:
            start_dt = (
                datetime.strptime(start_date, "%Y-%m-%d")
                if start_date else None
            )
            end_dt = (
                datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                if end_date else None
            )

            if start_dt and end_dt and start_dt >= end_dt:
                return (
                    "Start date must be before or equal to end date.",
                    400,
                )

        except ValueError:
            return "Invalid date format. Use YYYY-MM-DD.", 400

        def apply_dates(query, column):
            if start_dt:
                query = query.filter(column >= start_dt)
            if end_dt:
                query = query.filter(column < end_dt)
            return query

        def money(value):
            return f"Rs. {float(value or 0):,.2f}"

        def display_date(value):
            return value.strftime("%d-%m-%Y") if value else "-"

        headers = []
        rows = []

        # --------------------------------------------------
        # SALES REPORT
        # --------------------------------------------------
        if report_type == "sales":
            headers = [
                "Date", "Invoice", "Customer", "Amount", "Status"
            ]

            sales = apply_dates(
                Sale.query.order_by(Sale.sale_date.desc()),
                Sale.sale_date,
            ).all()

            for sale in sales:
                rows.append([
                    display_date(sale.sale_date),
                    sale.invoice_number or "-",
                    sale.customer.name if sale.customer else "Walk-in",
                    money(sale.total_amount),
                    (sale.status or "Unknown").replace("_", " ").title(),
                ])

        # --------------------------------------------------
        # INVENTORY REPORT
        # --------------------------------------------------
        
        # --------------------------------------------------
        # INVENTORY REPORT - INDIVIDUAL HANDSET INVENTORY
        # --------------------------------------------------
        elif report_type == "inventory":

            headers = [
                "IMEI 1",
                "IMEI 2",
                "Product Code",
                "Product",
                "Brand",
                "Variant",
                "Colour",
                "Condition",
                "Purchase Price",
                "Selling Price",
                "Stock Status",
            ]

            records = (
                IMEIRecord.query
                .filter_by(status="in_stock")
                .order_by(IMEIRecord.id.desc())
                .all()
            )

            for record in records:

                product = record.product

                condition_text = record.condition or "-"

                if (
                    record.used_grade
                    and str(condition_text).lower() == "used"
                ):
                    condition_text = (
                        f"{condition_text} ({record.used_grade})"
                    )

                rows.append([
                    record.imei1 or "-",
                    record.imei2 or "-",
                    product.product_code if product else "-",
                    product.name if product else "-",
                    product.brand if product and product.brand else "-",
                    record.variant or "-",
                    record.color or "-",
                    condition_text,
                    money(record.purchase_price),
                    money(record.selling_price),
                    "Available",
                ])

        # --------------------------------------------------
        # PURCHASE REPORT
        # --------------------------------------------------
        elif report_type == "purchases":
            headers = [
                "Date", "Invoice", "Supplier", "Amount", "Status"
            ]

            purchases = apply_dates(
                Purchase.query.order_by(Purchase.purchase_date.desc()),
                Purchase.purchase_date,
            ).all()

            for purchase in purchases:
                rows.append([
                    display_date(purchase.purchase_date),
                    purchase.invoice_number or "-",
                    purchase.supplier.name if purchase.supplier else "-",
                    money(purchase.total_amount),
                    (purchase.status or "Unknown").replace("_", " ").title(),
                ])

        # --------------------------------------------------
        # CUSTOMER REPORT
        # --------------------------------------------------
        elif report_type == "customers":
            headers = [
                "Name", "Phone", "Email", "GST Number", "Added"
            ]

            customers = Customer.query.order_by(Customer.name).all()

            if start_dt:
                customers = [
                    customer for customer in customers
                    if customer.created_at
                    and customer.created_at.replace(tzinfo=None) >= start_dt
                ]

            if end_dt:
                customers = [
                    customer for customer in customers
                    if customer.created_at
                    and customer.created_at.replace(tzinfo=None) < end_dt
                ]

            for customer in customers:
                rows.append([
                    customer.name or "-",
                    customer.phone or "-",
                    customer.email or "-",
                    customer.gst_number or "-",
                    display_date(customer.created_at),
                ])

        # --------------------------------------------------
        # EXPENSE REPORT
        # --------------------------------------------------
        elif report_type == "expenses":
            headers = [
                "Date", "Title", "Category", "Amount", "Notes"
            ]

            expenses = apply_dates(
                Expense.query.order_by(Expense.expense_date.desc()),
                Expense.expense_date,
            ).all()

            for expense in expenses:
                rows.append([
                    display_date(expense.expense_date),
                    expense.title or "-",
                    expense.category or "-",
                    money(expense.amount),
                    expense.notes or "-",
                ])

        # --------------------------------------------------
        # IMEI REPORT
        # --------------------------------------------------
        elif report_type == "imei":
            headers = [
                "IMEI Number 1",
                "IMEI Number 2",
                "Product Code",
                "Product",
                "Brand",
                "Status",
            ]

            records = IMEIRecord.query.order_by(
                IMEIRecord.id.desc()
            ).all()

            for record in records:
                product = record.product

                rows.append([
                    record.imei1 or "-",
                    record.imei2 or "-",
                    product.product_code if product else "-",
                    product.name if product else "-",
                    (
                        product.brand
                        if product and product.brand
                        else "-"
                    ),
                    (record.status or "Unknown").replace(
                        "_", " "
                    ).title(),
                ])

        # --------------------------------------------------
        # HANDSET STOCK AGEING REPORT
        # --------------------------------------------------
        elif report_type == "stock_ageing":
            headers = [
                "Age Group",
                "Handsets",
                "Purchase Value",
            ]

            today = datetime.now().date()

            records_query = IMEIRecord.query.filter(
                IMEIRecord.status == "in_stock"
            )

            if start_dt:
                records_query = records_query.filter(
                    IMEIRecord.purchase_date >= start_dt.date()
                )

            if end_dt:
                records_query = records_query.filter(
                    IMEIRecord.purchase_date < end_dt.date()
                )

            records = records_query.all()

            ageing_groups = {
                "0-29 Days": [],
                "30-59 Days": [],
                "60-89 Days": [],
                "90+ Days": [],
                "Purchase Date Missing": [],
            }

            for record in records:
                if not record.purchase_date:
                    ageing_groups["Purchase Date Missing"].append(record)
                    continue

                age_days = (today - record.purchase_date).days
                age_days = max(age_days, 0)

                if age_days < 30:
                    group = "0-29 Days"
                elif age_days < 60:
                    group = "30-59 Days"
                elif age_days < 90:
                    group = "60-89 Days"
                else:
                    group = "90+ Days"

                ageing_groups[group].append(record)

            for group_name, group_records in ageing_groups.items():
                purchase_value = sum(
                    float(record.purchase_price or 0)
                    for record in group_records
                )

                rows.append([
                    group_name,
                    str(len(group_records)),
                    money(purchase_value),
                ])

        # --------------------------------------------------
        # DAILY HANDSET SALES REPORT
        # --------------------------------------------------
        elif report_type == "daily_handset_sales":
            headers = [
                "Sale Date",
                "Handsets Sold",
                "Sales Value",
            ]

            sales_query = IMEIRecord.query.filter(
                IMEIRecord.status == "sold",
                IMEIRecord.sold_date.isnot(None),
            )

            sales_query = apply_dates(
                sales_query,
                IMEIRecord.sold_date,
            )

            records = sales_query.order_by(
                IMEIRecord.sold_date.asc()
            ).all()

            daily_totals = defaultdict(
                lambda: {"quantity": 0, "value": 0.0}
            )

            for record in records:
                sale_day = record.sold_date.date()

                daily_totals[sale_day]["quantity"] += 1
                daily_totals[sale_day]["value"] += float(
                    record.selling_price or 0
                )

            for sale_day in sorted(daily_totals):
                summary = daily_totals[sale_day]

                rows.append([
                    sale_day.strftime("%d-%m-%Y"),
                    str(summary["quantity"]),
                    money(summary["value"]),
                ])

        # --------------------------------------------------
        # MONTHLY HANDSET SALES REPORT
        # --------------------------------------------------
        elif report_type == "monthly_handset_sales":
            headers = [
                "Month",
                "Handsets Sold",
                "Sales Value",
            ]

            sales_query = IMEIRecord.query.filter(
                IMEIRecord.status == "sold",
                IMEIRecord.sold_date.isnot(None),
            )

            sales_query = apply_dates(
                sales_query,
                IMEIRecord.sold_date,
            )

            records = sales_query.order_by(
                IMEIRecord.sold_date.asc()
            ).all()

            monthly_totals = defaultdict(
                lambda: {"quantity": 0, "value": 0.0}
            )

            for record in records:
                month_key = record.sold_date.strftime("%Y-%m")

                monthly_totals[month_key]["quantity"] += 1
                monthly_totals[month_key]["value"] += float(
                    record.selling_price or 0
                )

            for month_key in sorted(monthly_totals):
                summary = monthly_totals[month_key]

                month_label = datetime.strptime(
                    month_key, "%Y-%m"
                ).strftime("%B %Y")

                rows.append([
                    month_label,
                    str(summary["quantity"]),
                    money(summary["value"]),
                ])

        # --------------------------------------------------
        # COMPLETE HANDSET REPORT
        # --------------------------------------------------
        

        # --------------------------------------------------
        # COMPLETE HANDSET REPORT
        # --------------------------------------------------
        elif report_type == "handset_summary":
            headers = [
                "IMEI 1",
                "IMEI 2",
                "Product",
                "Variant",
                "Colour",
                "Condition",
                "Purchase Date",
                "Purchase Price",
                "Selling Price",
                "Status",
                "Sold Date",
                "Invoice",
            ]

            records_query = IMEIRecord.query

            if start_dt or end_dt:
                records_query = records_query.filter(
                    IMEIRecord.purchase_date.isnot(None)
                )

                if start_dt:
                    records_query = records_query.filter(
                        IMEIRecord.purchase_date >= start_dt.date()
                    )

                if end_dt:
                    records_query = records_query.filter(
                        IMEIRecord.purchase_date < end_dt.date()
                    )

            records = records_query.order_by(
                IMEIRecord.purchase_date.desc()
            ).all()

            for record in records:
                product = record.product

                condition_text = record.condition or "-"

                if (
                    record.used_grade
                    and str(condition_text).lower() == "used"
                ):
                    condition_text = (
                        f"{condition_text} ({record.used_grade})"
                    )

                if record.status == "in_stock":
                    status_text = "Available"
                elif record.status == "sold":
                    status_text = "Sold"
                else:
                    status_text = (
                        (record.status or "Unknown")
                        .replace("_", " ")
                        .title()
                    )

                rows.append([
                    record.imei1 or "-",
                    record.imei2 or "-",
                    product.name if product else "-",
                    record.variant or "-",
                    record.color or "-",
                    condition_text,
                    display_date(record.purchase_date),
                    money(record.purchase_price),
                    money(record.selling_price),
                    status_text,
                    display_date(record.sold_date),
                    (
                        record.sale.invoice_number
                        if record.sale else "-"
                    ),
                ])


        # --------------------------------------------------
        # HANDLE EMPTY REPORTS
        # --------------------------------------------------
        if not rows:
            rows = [
                ["No records found for this report."]
                + [""] * (len(headers) - 1)
            ]

        # --------------------------------------------------
        # CREATE PDF
        # --------------------------------------------------
        buffer = BytesIO()

        page_size = (
            landscape(A4)
            if report_type in (
                "inventory",
                "customers",
                "imei",
                "stock_ageing",
                "daily_handset_sales",
                "monthly_handset_sales",
                "handset_summary",
             )
          else A4
        )

        document = SimpleDocTemplate(
            buffer,
            pagesize=page_size,
            rightMargin=30,
            leftMargin=30,
            topMargin=35,
            bottomMargin=35,
            title=allowed_reports[report_type],
        )

        styles = getSampleStyleSheet()

        story = [
            Paragraph(
                escape(allowed_reports[report_type]),
                styles["Title"],
            ),
            Spacer(1, 12),
        ]

        if start_date or end_date:
            period = (
                f"Period: {start_date or 'Beginning'} to "
                f"{end_date or 'Present'}"
            )

            story.extend([
                Paragraph(escape(period), styles["Normal"]),
                Spacer(1, 10),
            ])

        # Wrap cell contents so longer text fits inside the PDF.
        table_data = [
            [
                Paragraph(escape(str(header)), styles["Normal"])
                for header in headers
            ]
        ]

        cell_style = styles["BodyText"]

        if report_type == "handset_summary":
            cell_style = ParagraphStyle(
                "HandsetCell",
                parent=styles["BodyText"],
                fontSize=6.5,
                leading=7.5,
            )


        for row in rows:
            table_data.append([
                Paragraph(escape(str(value)), cell_style)
                for value in row
            ])

        available_width = page_size[0] - 60

        
        if report_type == "handset_summary":
            width_ratios = [
                1.5,  # IMEI 1
                1.5,  # IMEI 2
                1.6,  # Product
                1.2,  # Variant
                0.9,  # Colour
                1.1,  # Condition
                1.2,  # Purchase Date
                1.2,  # Purchase Price
                1.2,  # Selling Price
                1.0,  # Status
                1.2,  # Sold Date
                1.3,  # Invoice
            ]

            total_ratio = sum(width_ratios)

            column_widths = [
                available_width * ratio / total_ratio
                for ratio in width_ratios
            ]

        else:
            column_width = available_width / len(headers)
            column_widths = [column_width] * len(headers)

        table = Table(
    table_data,
    colWidths=column_widths,
    repeatRows=1,
    hAlign="LEFT",
)

        table.setStyle(
            TableStyle([
                (
                    "BACKGROUND",
                    (0, 0), (-1, 0),
                    colors.HexColor("#2457A7"),
                ),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                (
                    "ROWBACKGROUNDS",
                    (0, 1), (-1, -1),
                    [
                        colors.white,
                        colors.HexColor("#EEF4FC"),
                    ],
                ),
            ])
        )

        story.append(table)
        document.build(story)

        # --------------------------------------------------
        # SEND PDF DOWNLOAD RESPONSE
        # --------------------------------------------------
        pdf_data = buffer.getvalue()
        buffer.close()

        response = make_response(pdf_data)
        response.headers["Content-Type"] = "application/pdf"
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{report_type}_report.pdf"'
        )
        response.headers["Content-Length"] = str(len(pdf_data))
        response.headers["Cache-Control"] = "no-store"

        return response
   
    # --------------------------------------------------
    # ABOUT PAGE
    # --------------------------------------------------

    def get_app_launch_count():
        """Read the launch count from the same SQLite record we update at startup."""
        try:
            settings = db.session.get(StoreSettings, 1)
            if settings is None:
                return 0

            return int(settings.app_launch_count or 0)

        except Exception as e:
            print("Launch count error:", e)
            db.session.rollback()
            return 0


    @app.route("/about")
    @login_required
    def about_page():
        settings = db.session.get(StoreSettings, 1)

    # # Get application launch count
        launch_count = get_app_launch_count()

        license_status = license_manager.check_license_status()
        license_expiry_date = license_status.get("expiry")

        return render_template(
            "about.html",
            settings=settings,
            license_status=license_status,
            license_expiry_date=license_expiry_date,
            app_launch_count=launch_count,
            successfully_running_count=launch_count
        )
    # --------------------------------------------------
    # STORE SETTINGS
    # --------------------------------------------------

    @app.route("/store-logo/<filename>")
    @login_required
    def store_logo_file(filename):
        return send_from_directory(
            app.config["UPLOAD_FOLDER"],
            filename
        )

    
    # Real license status is provided by licensing.LicenseManager.

    @app.route("/store-settings", methods=["GET", "POST"])
    @login_required
    def store_settings_page():
        settings = db.session.get(StoreSettings, 1)

        if settings is None:
            settings = StoreSettings(id=1)
            db.session.add(settings)
            db.session.commit()

        if request.method == "POST":
            old_logo_filename = settings.logo_filename
            uploaded_logo_path = None

            try:
                store_name = request.form.get(
                    "store_name", ""
                ).strip()
                address = request.form.get("address", "").strip()
                phone = request.form.get("phone", "").strip()
                gstin = request.form.get("gstin", "").strip()
                paper_size = request.form.get(
                    "paper_size", "A4"
                ).strip()
                orientation = request.form.get(
                    "orientation", "portrait"
                ).strip()
                footer_text = request.form.get(
                    "footer_text", ""
                ).strip()
                
                opening_time = request.form.get("opening_time", "").strip()
                closing_time = request.form.get("closing_time", "").strip()

                invoice_prefix = request.form.get("invoice_prefix", "INV").strip()

                show_customer_id = request.form.get("show_customer_id") == "1"
                show_gstin = request.form.get("show_gstin") == "1"
                show_tax_breakdown = request.form.get("show_tax_breakdown") == "1"
                show_emi_details = request.form.get("show_emi_details") == "1"

                margin_top_mm = request.form.get("margin_top_mm", "10").strip()
                margin_bottom_mm = request.form.get("margin_bottom_mm", "10").strip()
                font_size = request.form.get("font_size", "10").strip()

                instagram_url = request.form.get(
                "instagram_url", ""
                ).strip()

                facebook_url = request.form.get(
                "facebook_url", ""
                ).strip()

                whatsapp_url = request.form.get(
                "whatsapp_url", ""
                ).strip()

                if not store_name:
                    raise ValueError("Store name is required.")

                if paper_size not in {
                    "A4", "A5", "thermal", "custom"
                }:
                    raise ValueError("Invalid paper size.")

                if orientation not in {
                    "portrait", "landscape"
                }:
                    raise ValueError("Invalid paper orientation.")

        
                # ------------------------------------------
                # LOGO UPLOAD / REPLACEMENT / REMOVAL
                # ------------------------------------------

                logo_file = request.files.get("store_logo")
                remove_logo = (
                    request.form.get("remove_logo") == "1"
                )

                new_logo_filename = None

                if logo_file and logo_file.filename:
                    safe_original_name = secure_filename(
                        logo_file.filename
                    )

                    extension = os.path.splitext(
                        safe_original_name
                    )[1].lower().lstrip(".")

                    allowed_extensions = app.config[
                        "ALLOWED_LOGO_EXTENSIONS"
                    ]

                    if extension not in allowed_extensions:
                        raise ValueError(
                            "Logo must be PNG, JPG, JPEG or WebP."
                        )

                    # Check actual file size
                    logo_file.stream.seek(0, os.SEEK_END)
                    file_size = logo_file.stream.tell()
                    logo_file.stream.seek(0)

                    if file_size == 0:
                        raise ValueError(
                            "The selected logo file is empty."
                        )

                    if file_size > app.config["MAX_LOGO_SIZE"]:
                        raise ValueError(
                            "Logo size must not exceed 2 MB."
                        )

                    # Generate a unique filename
                    new_logo_filename = (
                        f"{uuid.uuid4().hex}.{extension}"
                    )

                    uploaded_logo_path = os.path.join(
                        app.config["UPLOAD_FOLDER"],
                        new_logo_filename
                    )

                    logo_file.save(uploaded_logo_path)

                    # A newly uploaded logo takes priority
                    # over the remove checkbox.
                    settings.logo_filename = new_logo_filename

                elif remove_logo:
                    settings.logo_filename = None

                # ------------------------------------------
                # SAVE STORE INFORMATION
                # ------------------------------------------

                settings.store_name = store_name[:150]
                settings.address = address or None
                settings.phone = phone[:30] or None
                settings.gstin = gstin[:30] or None
                settings.paper_size = paper_size
                settings.orientation = orientation
                settings.footer_text = footer_text[:500] or None
                settings.instagram_url = instagram_url or None
                
                # BUSINESS HOURS
                settings.opening_time = opening_time or None
                settings.closing_time = closing_time or None

                # INVOICE PREFIX
                settings.invoice_prefix = invoice_prefix[:20] or "INV"

                # INVOICE DISPLAY OPTIONS
                settings.show_customer_id = show_customer_id
                settings.show_gstin = show_gstin
                settings.show_tax_breakdown = show_tax_breakdown
                settings.show_emi_details = show_emi_details

                # INVOICE LAYOUT
                try:
                    margin_top = float(margin_top_mm or 10)
                    margin_bottom = float(margin_bottom_mm or 10)
                    invoice_font_size = float(font_size or 10)
                except (TypeError, ValueError):
                    raise ValueError("Enter valid invoice margin and font size values.")

                if not all(map(math.isfinite, [
                    margin_top, margin_bottom, invoice_font_size
                ])):
                    raise ValueError("Invoice layout values must be valid numbers.")

                if not (0 <= margin_top <= 100 and 0 <= margin_bottom <= 100):
                    raise ValueError("Invoice margins must be between 0 and 100 mm.")

                if not (6 <= invoice_font_size <= 30):
                    raise ValueError("Invoice font size must be between 6 and 30.")

                settings.margin_top_mm = margin_top
                settings.margin_bottom_mm = margin_bottom
                settings.font_size = invoice_font_size
                settings.facebook_url = facebook_url or None
                settings.whatsapp_url = whatsapp_url or None

                # ------------------------------------------
                # CUSTOM PAPER DIMENSIONS
                # ------------------------------------------

                if paper_size == "custom":
                    try:
                        width = float(
                            request.form.get(
                                "custom_width_mm", "100"
                            )
                        )
                        height = float(
                            request.form.get(
                                "custom_height_mm", "150"
                            )
                        )
                    except (TypeError, ValueError):
                        raise ValueError(
                            "Enter valid custom paper dimensions."
                        )

                    if (
                        not math.isfinite(width)
                        or not math.isfinite(height)
                        or not 0 < width <= 1000
                        or not 0 < height <= 1000
                    ):
                        raise ValueError(
                            "Custom dimensions must be greater than "
                            "0 and no greater than 1000 mm."
                        )

                    settings.custom_width_mm = width
                    settings.custom_height_mm = height

                # Commit all settings together
                db.session.commit()

                # Remove old logo only after successful commit
                if (
                    old_logo_filename
                    and old_logo_filename != settings.logo_filename
                ):
                    old_logo_path = os.path.join(
                        app.config["UPLOAD_FOLDER"],
                        os.path.basename(old_logo_filename)
                    )

                    if os.path.isfile(old_logo_path):
                        try:
                            os.remove(old_logo_path)
                        except OSError:
                            current_app.logger.warning(
                                "Could not remove old store logo: %s",
                                old_logo_path
                            )

                flash(
                    "Store settings saved successfully.",
                    "success"
                )

                return redirect(
                    url_for("store_settings_page")
                )

            except ValueError as exc:
                db.session.rollback()

                # Delete newly uploaded file if saving failed
                if (
                    uploaded_logo_path
                    and os.path.isfile(uploaded_logo_path)
                ):
                    try:
                        os.remove(uploaded_logo_path)
                    except OSError:
                        current_app.logger.exception(
                            "Could not clean up uploaded logo."
                        )

                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()

                # Delete newly uploaded file if saving failed
                if (
                    uploaded_logo_path
                    and os.path.isfile(uploaded_logo_path)
                ):
                    try:
                        os.remove(uploaded_logo_path)
                    except OSError:
                        current_app.logger.exception(
                            "Could not clean up uploaded logo."
                        )

                current_app.logger.exception(
                    "Failed to save store settings."
                )

                flash(
                    "Could not save store settings. Please try again.",
                    "danger"
                )

            settings = db.session.get(StoreSettings, 1)

        return render_template(
            "store_settings.html",
            settings=settings,
        )

    @app.route("/inventory", methods=["GET", "POST"])
    @login_required
    def inventory_list():

        # Handle stock adjustment
        if request.method == "POST":
            product_id = request.form.get("product_id", type=int)
            action = request.form.get("action", "").strip().lower()
            quantity = request.form.get("quantity", type=int)
            reason = request.form.get("reason", "").strip()

            if not product_id or not quantity or quantity <= 0:
                flash("Please enter a valid product and quantity.", "warning")
                return redirect(url_for("inventory_list"))

            if action not in ("add", "remove"):
                flash("Invalid stock adjustment action.", "warning")
                return redirect(url_for("inventory_list"))

            if not reason:
                flash("Please enter a reason for the adjustment.", "warning")
                return redirect(url_for("inventory_list"))

            product = Product.query.filter_by(
                id=product_id,
                is_active=True
            ).with_for_update().first()

            if not product:
                flash("Product not found or inactive.", "warning")
                return redirect(url_for("inventory_list"))

            if product.track_imei:
                flash(
                    "Manual stock adjustment is disabled for IMEI-tracked products. "
                    "Manage handset stock through individual IMEI records.",
                    "warning"
                )
                return redirect(url_for("inventory_list"))

            current_stock = product.stock_quantity or 0

            if action == "remove" and quantity > current_stock:
                flash("Cannot remove more stock than available.", "warning")
                return redirect(url_for("inventory_list"))

            quantity_change = quantity if action == "add" else -quantity

            try:
                product.stock_quantity = current_stock + quantity_change

                movement = StockMovement(
                    product_id=product.id,
                    user_id=current_user.id,
                    movement_type=(
                        "adjustment_add"
                        if action == "add"
                        else "adjustment_remove"
                    ),
                    quantity_change=quantity_change,
                    reference="MANUAL-ADJUSTMENT",
                    notes=reason
                )

                db.session.add(movement)
                db.session.commit()

                flash(
                    f"Stock updated successfully. New stock: "
                    f"{product.stock_quantity}",
                    "success"
                )

            except Exception:
                db.session.rollback()
                flash("Unable to update stock. Please try again.", "danger")

            return redirect(url_for("inventory_list"))

        # Search and filters
        search = request.args.get("q", "").strip()

        # Product status filter
        status = request.args.get("status", "active").strip().lower()

        if status not in ("active", "inactive", "all"):
            status = "active"

        # Stock status filter
        stock_status = request.args.get("stock", "all").strip().lower()

        if stock_status not in ("in_stock", "low", "out", "all"):
            stock_status = "all"

        query = Product.query

        # Active / Inactive filter
        if status == "active":
            query = query.filter(Product.is_active.is_(True))

        elif status == "inactive":
            query = query.filter(Product.is_active.is_(False))

        # Search filter
        if search:
            pattern = f"%{search}%"

            query = query.filter(
                or_(
                    Product.name.ilike(pattern),
                    Product.brand.ilike(pattern),
                    Product.model.ilike(pattern),
                    Product.product_code.ilike(pattern),
                    Product.barcode.ilike(pattern),
                    Product.category.ilike(pattern)
                )
            )


        # Get all available IMEI handset counts
        imei_stock_counts = dict(
            db.session.query(
                IMEIRecord.product_id,
                db.func.count(IMEIRecord.id)
            )
            .filter(
                IMEIRecord.is_active.is_(True),
                IMEIRecord.status == "in_stock",
                IMEIRecord.sale_id.is_(None)
            )
            .group_by(IMEIRecord.product_id)
            .all()
        )

        # Get supplier-purchased IMEI handset counts only
        supplier_imei_stock_counts = dict(
            db.session.query(
                IMEIRecord.product_id,
                db.func.count(IMEIRecord.id)
            )
            .filter(
                IMEIRecord.is_active.is_(True),
                IMEIRecord.status == "in_stock",
                IMEIRecord.sale_id.is_(None),
                db.or_(
                    IMEIRecord.source == "supplier",
                    IMEIRecord.supplier_id.isnot(None),
                )
            )
            .group_by(IMEIRecord.product_id)
            .all()
        )

        # Calculate actual available stock
        active_products = Product.query.filter(
            Product.is_active.is_(True)
        ).all()

        available_stock = {}
        reorder_stock = {}

        total_units = 0
        low_stock = 0
        out_of_stock = 0

        for product in active_products:
            if product.track_imei:
                # All handsets count toward total available stock
                quantity = imei_stock_counts.get(product.id, 0)

                # Only supplier handsets count toward reorder alerts
                reorder_quantity = supplier_imei_stock_counts.get(product.id, 0)
            else:
                quantity = max(int(product.stock_quantity or 0), 0)
                reorder_quantity = quantity

            available_stock[product.id] = quantity
            reorder_stock[product.id] = reorder_quantity

            total_units += quantity

            reorder_level = max(int(product.reorder_level or 0), 0)

            if quantity == 0:
                out_of_stock += 1
            elif 0 < reorder_quantity <= reorder_level:
                low_stock += 1

        total_products = len(active_products)

        products = query.all()

        # Apply stock filter using the correct stock quantity
        stock_status = request.args.get("stock", "all").strip().lower()

        if stock_status != "all":
            if stock_status == "in_stock":
                products = [
                    p for p in products
                    if p.is_active
                    and reorder_stock.get(p.id, 0) > max(int(p.reorder_level or 0), 0)
                ]

            elif stock_status == "low":
                products = [
                    p for p in products
                    if p.is_active
                    and 0 < reorder_stock.get(p.id, 0)
                    <= max(int(p.reorder_level or 0), 0)
                ]

            elif stock_status == "out":
                products = [
                    p for p in products
                    if p.is_active and available_stock.get(p.id, 0) == 0
                ]

        latest_suppliers = {}

        for product in products:
            latest_record = (
                IMEIRecord.query
                .filter_by(product_id=product.id)
                .filter(IMEIRecord.supplier_id.isnot(None))
                .order_by(
                    IMEIRecord.purchase_date.desc(),
                    IMEIRecord.created_at.desc()
                )
                .first()
            )

            latest_suppliers[product.id] = (
                latest_record.supplier if latest_record else None
            )

        return render_template(
            "inventory/list.html",
            products=products,
            search=search,
            status=status,
            stock_status=stock_status,
            total_products=total_products,
            total_units=total_units,
            low_stock=low_stock,
            out_of_stock=out_of_stock,
            latest_suppliers=latest_suppliers,
            available_stock=available_stock,
        )

    @app.route("/products/<int:product_id>/status", methods=["POST"])
    @login_required
    def product_set_status(product_id):
        product = db.session.get(Product, product_id)

        if product is None:
            flash("Product not found.", "danger")
            return redirect(url_for("products_list"))

        try:
            new_status = request.form.get("is_active", "").strip()

            if new_status not in ("0", "1"):
                raise ValueError("Invalid product status.")

            product.is_active = (new_status == "1")
            db.session.commit()

            if product.is_active:
                flash("Product activated successfully.", "success")
            else:
                flash("Product deactivated successfully.", "success")

        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        except Exception:
            db.session.rollback()
            app.logger.exception("Product status update failed")
            flash("Could not update product status.", "danger")

        return redirect(url_for("products_list"))

    @app.route("/handsets/<int:imei_id>/toggle-status", methods=["POST"])
    @login_required
    def handset_toggle_status(imei_id):

        handset = db.session.get(IMEIRecord, imei_id)

        if handset is None:
            flash("Handset not found.", "danger")
            return redirect(request.referrer or url_for("products_list"))

        try:
            if handset.is_active:
                handset.is_active = False
                message = "Handset deactivated successfully."
            else:
                if handset.status != "in_stock" or handset.sale_id is not None:
                    flash(
                        "Sold handsets cannot be activated through this action.",
                        "warning"
                    )
                    return redirect(request.referrer or url_for("products_list"))

                handset.is_active = True
                message = "Handset activated successfully."

            db.session.commit()
            flash(message, "success")

        except Exception:
            db.session.rollback()
            current_app.logger.exception("Handset status update failed")
            flash("Could not update handset status.", "danger")

        return redirect(request.referrer or url_for("products_list"))

    @app.route("/inventory/movements")
    @login_required
    def inventory_movements():

        movements = StockMovement.query.order_by(
            StockMovement.created_at.desc()
        ).all()

        return render_template(
            "inventory/movements.html",
            movements=movements
        )

    @app.route("/inventory/handsets")
    @login_required
    def inventory_handsets():
        search = request.args.get("q", "").strip()
        status = request.args.get("status", "all").strip().lower()
        brand = request.args.get("brand", "").strip()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()

        supplier_id = request.args.get("supplier_id", type=int)

        suppliers = (
            Supplier.query
            .filter_by(is_active=True)
            .order_by(Supplier.name.asc())
            .all()
        )

        query = IMEIRecord.query.join(Product)

        if search:
            pattern = f"%{search}%"
            query = query.filter(or_(
                IMEIRecord.imei1.ilike(pattern),
                IMEIRecord.imei2.ilike(pattern),
                Product.name.ilike(pattern),
                Product.brand.ilike(pattern),
                Product.model.ilike(pattern),
                Product.variant.ilike(pattern),
            ))

        if status in ("in_stock", "sold"):
            query = query.filter(IMEIRecord.status == status)
        else:
            status = "all"

        if brand:
            query = query.filter(Product.brand == brand)

        if date_from:
            try:
                start_date = datetime.strptime(date_from, "%Y-%m-%d").date()
                query = query.filter(IMEIRecord.purchase_date >= start_date)
            except ValueError:
                date_from = ""

        if date_to:
            try:
                end_date = datetime.strptime(date_to, "%Y-%m-%d").date()
                query = query.filter(IMEIRecord.purchase_date <= end_date)
            except ValueError:
                date_to = ""

        handsets = query.order_by(
            IMEIRecord.purchase_date.desc(),
            IMEIRecord.created_at.desc()
        ).all()

        total_handsets = IMEIRecord.query.count()
        available_count = IMEIRecord.query.filter_by(status="in_stock").count()
        sold_count = IMEIRecord.query.filter_by(status="sold").count()

        brands = (
            db.session.query(Product.brand)
            .join(IMEIRecord, IMEIRecord.product_id == Product.id)
            .filter(Product.brand.isnot(None), Product.brand != "")
            .distinct()
            .order_by(Product.brand.asc())
            .all()
        )

        settings = StoreSettings.query.first()

        return render_template(
            "inventory/handsets.html",
            handsets=handsets,
            search=search,
            status=status,
            brand=brand,
            date_from=date_from,
            date_to=date_to,
            brands=[item[0] for item in brands],
            total_handsets=total_handsets,
            available_count=available_count,
            sold_count=sold_count,
            settings=settings,
        )
    

        # --------------------------------------------------
        # ADMIN: REPORTS
        # --------------------------------------------------

    @app.route("/reports")
    @login_required
    @roles_required("admin")
    def reports_page():
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()

        query = Sale.query

        try:
            query = apply_date_filters(query, Sale, date_from, date_to)
        except ValueError:
            flash("Please enter valid report dates.", "warning")
            return redirect(url_for("reports_page"))

        sales = query.order_by(Sale.id.desc()).all()

        payments = Payment.query.filter_by(status="completed").all()

        paid_by_sale = {}
        for payment in payments:
            paid_by_sale[payment.sale_id] = paid_by_sale.get(
                payment.sale_id, 0.0
            ) + float(payment.amount or 0)

        report_total = sum(float(sale.total_amount or 0) for sale in sales)

        report_paid = sum(paid_by_sale.get(sale.id, 0.0) for sale in sales)

        return render_template(
            "admin/reports.html",
            sales=sales,
            date_from=date_from,
            date_to=date_to,
            report_count=len(sales),
            report_total=report_total,
            report_paid=report_paid,
            report_balance=max(report_total - report_paid, 0.0),
            paid_by_sale=paid_by_sale,
        )

    # --------------------------------------------------
    # ADMIN: USER ACCOUNTS
    # --------------------------------------------------

    @app.route("/users")
    @login_required
    @roles_required("admin")
    def users_accounts_page():
        users = User.query.order_by(User.id.asc()).all()

        return render_template(
            "admin/users.html",
            users=users,
        )

    @app.route("/users/<int:user_id>/toggle-status", methods=["POST"])
    @login_required
    @roles_required("admin")
    def toggle_user_status(user_id):
        user = db.session.get(User, user_id)

        if user is None:
            flash("User not found.", "warning")
            return redirect(url_for("users_accounts_page"))

        if (user.role or "").lower() == "legacy":
            flash("Historical records cannot be activated or modified.", "warning")
            return redirect(url_for("users_accounts_page"))

        # Protect every admin account.
        if (user.role or "").lower() == "admin":
            flash("Admin accounts cannot be deactivated.", "danger")
            return redirect(url_for("users_accounts_page"))

        # Prevent changing the currently logged-in account.
        if user.id == current_user.id:
            flash("You cannot change your own account status.", "danger")
            return redirect(url_for("users_accounts_page"))

        try:
            user.is_active = not user.is_active
            db.session.commit()

            status = "activated" if user.is_active else "deactivated"
            flash(f"Staff account {status} successfully.", "success")

        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to update staff account status.")
            flash("Could not update staff account status.", "danger")

        return redirect(url_for("users_accounts_page"))


    # --------------------------------------------------
    # LOGIN MANAGER
    # --------------------------------------------------

    @login_manager.user_loader
    def load_user(user_id):
        try:
            user = db.session.get(User, int(user_id))

            if user and user.is_active:
                return user

            return None

        except (ValueError, TypeError):
            return None

    # --------------------------------------------------
    # SHARED HELPERS
    # --------------------------------------------------

    def apply_date_filters(query, model, date_from, date_to):
        """Apply date filters when the model has created_at."""
        if not hasattr(model, "created_at"):
            if date_from or date_to:
                flash(
                    "Date filtering is unavailable for this record type.",
                    "warning",
                )
            return query

        try:
            if date_from:
                start = datetime.strptime(date_from, "%Y-%m-%d").date()
                query = query.filter(
                    model.created_at >= datetime.combine(start, time.min)
                )

            if date_to:
                end = datetime.strptime(date_to, "%Y-%m-%d").date()
                query = query.filter(
                    model.created_at <= datetime.combine(end, time.max)
                )

        except ValueError:
            raise ValueError("Please enter valid filter dates.")

        return query

    def parse_number(field_name, default, converter):
        """Read and validate a numeric form field."""
        raw = request.form.get(field_name, "").strip()

        if raw == "":
            return default

        try:
            value = converter(raw)
        except (ValueError, TypeError):
            raise ValueError(f"Enter a valid value for {field_name.replace('_', ' ')}.")

        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"Enter a valid value for {field_name.replace('_', ' ')}.")

        return value

    # --------------------------------------------------
    # PRODUCT FORM VALIDATION HELPER
    # --------------------------------------------------

    def product_form_values(require_used_grade=True, existing_product=None):
        """Validate and return values matching the Product model."""

        product_code = request.form.get("product_code", "").strip()
        name = request.form.get("name", "").strip()
        condition = request.form.get("condition", "Sealed").strip().title()

        if condition not in {"Sealed", "Used"}:
            raise ValueError("Choose Sealed or Used.")

        if not product_code:
            while True:
                product_code = (
                    f"PRD-{datetime.now().strftime('%y%m%d%H%M%S')}-"
                    f"{os.urandom(2).hex().upper()}"
                )
                if not Product.query.filter_by(product_code=product_code).first():
                    break

        if len(product_code) > 50:
            raise ValueError("Product code must be 50 characters or fewer.")

        if not name:
            raise ValueError("Product name is required.")

        if len(name) > 150:
            raise ValueError("Product name must be 150 characters or fewer.")

        def number(field, default=0.0):
            raw = request.form.get(field, "").strip()
            if not raw:
                return float(default)

            try:
                value = float(raw)
            except (ValueError, TypeError, OverflowError):
                raise ValueError(f"Enter a valid number for {field.replace('_', ' ')}.")

            if not math.isfinite(value):
                raise ValueError(f"Enter a valid number for {field.replace('_', ' ')}.")

            return value

        def integer(field, default=0):
            value = number(field, default)
            if not value.is_integer():
                raise ValueError(
                    f"{field.replace('_', ' ').title()} must be a whole number."
                )
            return int(value)

        cost_price = number(
            "cost_price",
            getattr(existing_product, "cost_price", 0) or 0
        )

        selling_price = number(
            "selling_price",
            getattr(existing_product, "selling_price", 0) or 0
        )

        gst_percent = number(
            "gst_percent",
            getattr(existing_product, "gst_percent", 0) or 0
        )

        stock_quantity = (
            existing_product.stock_quantity
            if existing_product is not None
            else 0
        )

        reorder_default = (
            existing_product.reorder_level
            if existing_product is not None
            else 5
        )

        reorder_level = integer("reorder_level", reorder_default)

        warranty_months = (
            existing_product.warranty_months
            if existing_product is not None
            else 0
        )

        if cost_price < 0 or selling_price < 0:
            raise ValueError("Prices cannot be negative.")

        if not 0 <= gst_percent <= 100:
            raise ValueError("GST must be between 0 and 100.")

        if stock_quantity < 0 or reorder_level < 0:
            raise ValueError("Stock and reorder level cannot be negative.")

        if not 0 <= warranty_months <= 120:
            raise ValueError("Warranty must be between 0 and 120 months.")

        used_grade = request.form.get("used_grade", "").strip() or None

        if condition == "Used":
            allowed_grades = {"Excellent", "Good", "Fair", "For Parts"}

            if used_grade and used_grade not in allowed_grades:
                raise ValueError("Choose a valid grade for the used phone.")

            if require_used_grade and not used_grade:
                raise ValueError("Choose a valid grade for the used phone.")
        else:
            used_grade = None

        barcode = request.form.get("barcode", "").strip() or None
        if barcode and len(barcode) > 100:
            raise ValueError("Barcode must be 100 characters or fewer.")

        return {
            "product_code": product_code,
            "barcode": barcode,
            "name": name,
            "brand": request.form.get("brand", "").strip() or None,
            "model": request.form.get("model", "").strip() or None,
            "category": request.form.get("category", "Mobile").strip() or "Mobile",
            "condition": condition,
            "used_grade": used_grade,
            "warranty_months": warranty_months,
            "hsn_code": (
                request.form.get("hsn_code", "").strip()
                or (
                    getattr(existing_product, "hsn_code", None)
                    if existing_product is not None
                    else None
                )
            ),
            "gst_percent": gst_percent,
            "cost_price": cost_price,
            "selling_price": selling_price,
            "stock_quantity": stock_quantity,
            "reorder_level": reorder_level,
            "track_imei": (
                 existing_product.track_imei
                 if existing_product is not None
                 else True
            ),
            
            "is_active": (
                existing_product.is_active
                if existing_product is not None
                else True
            ),
        }

    # Share the validator with the standalone direct-stock view function.
    app.extensions["product_form_values"] = product_form_values
    
    # --------------------------------------------------
    # CREATE USER - ADMIN ONLY (NEW)
    # --------------------------------------------------

    @app.route("/users/create", methods=["GET", "POST"])
    @login_required
    @roles_required("admin")
    def create_user():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            full_name = request.form.get("full_name", "").strip()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")
            role = request.form.get("role", "cashier").strip().lower()

            allowed_roles = {"admin", "manager", "cashier", "staff"}

            if not username or len(username) > 80:
                flash("Enter a valid username.", "danger")
            elif not full_name or len(full_name) > 150:
                flash("Enter the user's full name.", "danger")
            elif len(password) < 8:
                flash("Password must contain at least 8 characters.", "danger")
            elif password != confirm_password:
                flash("Passwords do not match.", "danger")
            elif role not in allowed_roles:
                flash("Select a valid role.", "danger")
            elif User.query.filter(
                db.func.lower(User.username) == username.lower()
            ).first():
                flash("Username already exists.", "danger")
            else:
                user = User(
                    username=username,
                    full_name=full_name,
                    role=role,
                    is_active=True,
                )
                user.set_password(password)

                try:
                    db.session.add(user)
                    db.session.commit()
                    flash("User created successfully.", "success")
                    return redirect(url_for("users_accounts_page"))
                except Exception:
                    db.session.rollback()
                    app.logger.exception("Admin user creation failed")
                    flash("Could not create the user.", "danger")

        return render_template("admin/user_create.html")
        
    # --------------------------------------------------
    # ADMIN: ANALYTICS
    # --------------------------------------------------
    @app.route("/analytics")
    @login_required
    @roles_required("admin")
    def analytics_page():
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()

        sales_query = Sale.query

        if start_date:
            try:
                datetime.strptime(start_date, "%Y-%m-%d")
                sales_query = sales_query.filter(
                    func.date(Sale.sale_date) >= start_date
                )
            except ValueError:
                start_date = ""

        if end_date:
            try:
                datetime.strptime(end_date, "%Y-%m-%d")
                sales_query = sales_query.filter(
                    func.date(Sale.sale_date) <= end_date
                )
            except ValueError:
                end_date = ""

        sales_records = sales_query.order_by(
            Sale.sale_date.desc()
        ).all()

        transaction_count = len(sales_records)

        total_sales = sum(
            float(sale.total_amount or 0)
            for sale in sales_records
        )

        average_sale = (
            total_sales / transaction_count
            if transaction_count else 0
        )

        sales_rows = []
        profit_rows = []
        total_profit = 0

        for sale in sales_records:
            revenue = 0
            cost = 0

            items = SaleItem.query.filter_by(
                sale_id=sale.id
            ).all()

            for item in items:
                quantity = float(item.quantity or 0)
                unit_price = float(item.unit_price or 0)
                revenue += quantity * unit_price

                product = getattr(item, "product", None)
                if product:
                    cost_price = float(
                        getattr(product, "cost_price", 0) or 0
                    )
                    cost += quantity * cost_price

            profit = revenue - cost
            total_profit += profit

            paid_amount = db.session.query(
                func.coalesce(func.sum(Payment.amount), 0)
            ).filter(
                Payment.sale_id == sale.id,
                Payment.status == "completed"
            ).scalar()

            paid_amount = float(paid_amount or 0)
            sale_amount = float(sale.total_amount or 0)

            if paid_amount >= sale_amount and sale_amount > 0:
                payment_status = "Paid"
            elif paid_amount > 0:
                payment_status = "Partial"
            else:
                payment_status = "Unpaid"

            invoice_no = (
                getattr(sale, "invoice_number", None)
                or sale.id
            )

            sales_rows.append({
                "id": sale.id,
                "date": sale.sale_date,
                "invoice_no": invoice_no,
                "customer": getattr(sale, "customer", None),
                "total_amount": sale_amount,
                "payment_status": payment_status,
            })

            profit_rows.append({
                "id": sale.id,
                "date": sale.sale_date,
                "invoice_no": invoice_no,
                "revenue": revenue,
                "cost": cost,
                "profit": profit,
            })

        return render_template(
            "admin/analytics.html",
            total_sales=total_sales,
            total_profit=total_profit,
            transaction_count=transaction_count,
            average_sale=average_sale,
            sales_rows=sales_rows,
            profit_rows=profit_rows,
            start_date=start_date,
            end_date=end_date,
            total_customers=Customer.query.count(),
            total_products=Product.query.count(),
            total_users=User.query.count(),
        )

    # --------------------------------------------------
    # PRODUCTS: DEACTIVATE
    # --------------------------------------------------

    @app.route("/products/<int:product_id>/deactivate", methods=["POST"])
    @login_required
    def product_deactivate(product_id):
        product = db.session.get(Product, product_id)

        if product is None:
            flash("Product not found.", "danger")
            return redirect(url_for("products_list"))

        product.is_active = False

        try:
            db.session.commit()
            flash(f"{product.name} has been deactivated.", "success")
        except Exception:
            db.session.rollback()
            app.logger.exception("Product deactivation failed")
            flash("Could not deactivate the product.", "danger")

        return redirect(url_for("products_list"))

    # --------------------------------------------------
    # PRODUCTS: CREATE
    # --------------------------------------------------

    @app.route("/products/create", methods=["GET", "POST"])
    @login_required
    def product_create():
        selected_type = (
            (request.args.get("type") or request.form.get("condition") or "Sealed")
            .strip()
            .title()
        )

        if selected_type not in {"Sealed", "Used"}:
            selected_type = "Sealed"

        if request.method == "POST":
            try:
                values = current_app.extensions["product_form_values"]()

                # Stock is added only through purchase receiving.
                values["stock_quantity"] = 0

                product = Product(**values)
                db.session.add(product)
                db.session.commit()

                flash("Product created successfully.", "success")
                return redirect(url_for("products_list"))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                app.logger.exception("Product creation failed")
                flash(
                    "Could not save the product. "
                    "Check the application log for details.",
                    "danger",
                )

        return render_template(
            "products/form.html",
            product=None,
            selected_type=selected_type,
        )

    @app.route("/products/edit/<int:product_id>", methods=["GET", "POST"])
    @login_required
    def product_edit(product_id):
        product = db.session.get(Product, product_id)

        if product is None:
            flash("Product not found.", "danger")
            return redirect(url_for("products_list"))

        selected_type = product.condition or "Sealed"

        if request.method == "POST":
            try:
                values = current_app.extensions["product_form_values"](
                    existing_product=product
                )

                # Stock must only change through purchase receiving or stock adjustment.
                values["stock_quantity"] = product.stock_quantity

                # Prevent duplicate product codes on other products.
                existing_code = Product.query.filter(
                    Product.product_code == values["product_code"],
                    Product.id != product.id,
                ).first()

                if existing_code:
                    raise ValueError("This product code already exists.")

                # Prevent duplicate barcodes on other products.
                if values.get("barcode"):
                    existing_barcode = Product.query.filter(
                        Product.barcode == values["barcode"],
                        Product.id != product.id,
                    ).first()

                    if existing_barcode:
                        raise ValueError("This barcode already exists.")

                # Update the existing product.
                for field, value in values.items():
                    setattr(product, field, value)

                db.session.commit()

                flash("Product updated successfully.", "success")
                return redirect(url_for("products_list"))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                app.logger.exception("Product update failed")

                flash(
                    "Could not update the product. "
                    "Check the application log for details.",
                    "danger",
                )

        return render_template(
            "products/form.html",
            product=product,
            selected_type=selected_type,
        )

    @app.route("/products/view/<int:product_id>")
    @login_required
    def product_view(product_id):
        product = db.session.get(Product, product_id)

        if product is None:
            flash("Product not found.", "danger")
            return redirect(url_for("products_list"))

        imei_records = (
            IMEIRecord.query
            .filter_by(product_id=product.id)
            .order_by(IMEIRecord.id.desc())
            .all()
        )

        return render_template(
            "products/view.html",
            product=product,
            imei_records=imei_records,
        )

    @app.route("/products/delete/<int:product_id>", methods=["POST"])
    @login_required
    def product_delete(product_id):
        product = db.session.get(Product, product_id)

        if product is None:
            flash("Product not found.", "danger")
            return redirect(url_for("products_list"))

        try:
            # Do not permanently delete products with stock or history.
            has_imei_records = IMEIRecord.query.filter_by(
                product_id=product.id
            ).first()

            has_purchase_records = PurchaseItem.query.filter_by(
                product_id=product.id
            ).first()

            has_sales_records = SaleItem.query.filter_by(
                product_id=product.id
            ).first()

            has_stock_movements = StockMovement.query.filter_by(
                product_id=product.id
            ).first()
            

            if (
                (product.stock_quantity or 0) > 0
                or has_imei_records
                or has_purchase_records
                or has_sales_records
                or has_stock_movements
            ):
                product.is_active = False
                db.session.commit()

                flash(
                    "Product deactivated successfully. Its transaction history has been preserved.",
                    "success",
                )
                return redirect(url_for("products_list"))

            db.session.delete(product)
            db.session.commit()

            flash("Product deleted successfully.", "success")

        except Exception:
            db.session.rollback()
            app.logger.exception("Product deletion failed")
            flash("Could not delete the product.", "danger")

        return redirect(url_for("products_list"))

    # --------------------------------------------------
    # PRODUCTS: BARCODE LOOKUP
    # --------------------------------------------------

    @app.route("/products/barcode-lookup")
    @login_required
    def product_barcode_lookup():
        barcode = request.args.get("barcode", "").strip()

        if not barcode:
            return (
                jsonify(
                    {
                        "found": False,
                        "message": "Enter or scan a barcode.",
                    }
                ),
                400,
            )

        product = Product.query.filter_by(barcode=barcode).first()

        if not product:
            return jsonify(
                {
                    "found": False,
                    "message": "Barcode not found. You can enter the details manually.",
                }
            )

        return jsonify(
            {
                "found": True,
                "product": {
                    "product_code": product.product_code,
                    "barcode": product.barcode or "",
                    "name": product.name or "",
                    "brand": product.brand or "",
                    "model": product.model or "",
                    "category": product.category or "Mobile",
                    "variant": product.variant or "",
                    "color": product.color or "",
                    "condition": product.condition or "Sealed",
                    "used_grade": product.used_grade or "",
                },
            }
        )

    # --------------------------------------------------
    # IMEI RECORDS: LIST AND TRACKING
    # --------------------------------------------------

    @app.route("/imei-records")
    @login_required
    def imei_records_list():
        search = request.args.get("search", "").strip()
        product_id = request.args.get("product_id", type=int)
    
        # Individual handset activation
        status = request.args.get("status", "active").strip().lower()
        if status not in ("active", "inactive", "all"):
            status = "active"
    
        # Handset condition
        product_type = (
            request.args.get("product_type")
            or request.args.get("type")
            or ""
        ).strip().title()
    
        category = request.args.get("category", "").strip()
        supplier_id = request.args.get("supplier_id", type=int)
    
        contact_type = request.args.get(
            "contact_type", "all"
        ).strip().lower()
    
        if contact_type not in ("all", "supplier", "seller"):
            contact_type = "all"
    
        # Individual handset stock status
        stock = request.args.get("stock", "").strip().lower()

        source = request.args.get("source", "").strip().lower()

        allowed_sources = ("direct", "purchase")

        if source not in allowed_sources:
            source = ""
    
        allowed_stock = {
            "in_stock",
            "sold",
        }
    
        if stock not in allowed_stock:
            stock = ""
    
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
    
        # Parse purchase dates
        from_date = None
        to_date = None
    
        if date_from:
            try:
                from_date = datetime.strptime(
                    date_from, "%Y-%m-%d"
                ).date()
            except ValueError:
                date_from = ""
    
        if date_to:
            try:
                to_date = datetime.strptime(
                    date_to, "%Y-%m-%d"
                ).date()
            except ValueError:
                date_to = ""
    
        # Base query: include handsets even if their parent
        # Product is inactive.
        query = IMEIRecord.query.join(
            Product,
            IMEIRecord.product_id == Product.id,
        ).outerjoin(
            Supplier,
            IMEIRecord.supplier_id == Supplier.id,
        ).outerjoin(
            UsedHandsetSeller,
            UsedHandsetSeller.imei_record_id == IMEIRecord.id,
        )

        # Filter handsets for the selected product
        if product_id:
            query = query.filter(IMEIRecord.product_id == product_id)
    
        # Active / inactive individual handset filter
        if status == "active":
            query = query.filter(IMEIRecord.is_active.is_(True))
        elif status == "inactive":
            query = query.filter(IMEIRecord.is_active.is_(False))
    
        # Sealed / Used filter
        if product_type in ("Sealed", "Used"):
            query = query.filter(
                db.func.lower(
                    db.func.trim(IMEIRecord.condition)
                ) == product_type.lower()
            )
        else:
            product_type = ""
    
        # Search product, handset, supplier and seller details
        if search:
            pattern = f"%{search}%"
    
            query = query.filter(
                db.or_(
                    Product.name.ilike(pattern),
                    Product.product_code.ilike(pattern),
                    Product.brand.ilike(pattern),
                    Product.model.ilike(pattern),
                    Product.category.ilike(pattern),
    
                    IMEIRecord.imei1.ilike(pattern),
                    IMEIRecord.imei2.ilike(pattern),
                    IMEIRecord.variant.ilike(pattern),
                    IMEIRecord.color.ilike(pattern),
                    IMEIRecord.condition.ilike(pattern),
                    IMEIRecord.used_grade.ilike(pattern),
    
                    Supplier.name.ilike(pattern),
                    Supplier.company_name.ilike(pattern),
                    Supplier.contact_person.ilike(pattern),
                    Supplier.phone.ilike(pattern),
                    Supplier.mobile_number_2.ilike(pattern),
    
                    UsedHandsetSeller.seller_name.ilike(pattern),
                    UsedHandsetSeller.seller_phone.ilike(pattern),
                    UsedHandsetSeller.seller_address.ilike(pattern),
                )
            )
    
        # Category filter
        if category:
            query = query.filter(Product.category == category)
    
        # Supplier filter
        if supplier_id:
            query = query.filter(
                IMEIRecord.supplier_id == supplier_id
            )
    
        # Contact type filter
        # Stock source filter
        if contact_type == "supplier":
            query = query.filter(
                IMEIRecord.supplier_id.isnot(None)
            )

        elif contact_type == "seller":
            query = query.filter(
                IMEIRecord.id.in_(
                    db.session.query(
                        UsedHandsetSeller.imei_record_id
                ).filter(
                        UsedHandsetSeller.imei_record_id.isnot(None)
                )
            )
        )
    
        # Handset stock status filter
        if stock:
            query = query.filter(IMEIRecord.status == stock)

        # Stock source filter
        if source:
            query = query.filter(
                db.func.lower(IMEIRecord.source) == source
            )
    
        # Purchase date filters
        if from_date:
            query = query.filter(
                IMEIRecord.purchase_date >= from_date
            )
    
        if to_date:
            query = query.filter(
                IMEIRecord.purchase_date <= to_date
            )
    
        records = query.order_by(
            IMEIRecord.id.desc()
        ).all()
    
        # Overall handset summary counts
        counts = {
            "total": IMEIRecord.query.count(),
    
            "active": IMEIRecord.query.filter_by(
                is_active=True
            ).count(),
    
            "inactive": IMEIRecord.query.filter_by(
                is_active=False
            ).count(),
    
            "in_stock": IMEIRecord.query.filter_by(
                status="in_stock"
            ).count(),
    
            "sold": IMEIRecord.query.filter_by(
                status="sold"
            ).count(),
    
            # "returned": IMEIRecord.query.filter_by(
            #     status="returned"
            # ).count(),
    
            # "damaged": IMEIRecord.query.filter_by(
            #     status="damaged"
            # ).count(),
        }
    
        # Product-wise summary cards from filtered handsets
        product_summary_map = {}
    
        for record in records:
            product = record.product
    
            if product.id not in product_summary_map:
                product_summary_map[product.id] = {
                    "product": product,
                    "count": 0,
                    "in_stock": 0,
                    "variants": set(),
                }
    
            item = product_summary_map[product.id]
            item["count"] += 1
    
            if record.status == "in_stock":
                item["in_stock"] += 1
    
            if record.variant:
                item["variants"].add(record.variant)
    
        product_summaries = list(
            product_summary_map.values()
        )
    
        suppliers = Supplier.query.order_by(
            Supplier.name.asc()
        ).all()
    
        categories = [
            row[0]
            for row in db.session.query(
                Product.category
            ).filter(
                Product.category.isnot(None),
                Product.category != "",
            ).distinct().order_by(
                Product.category.asc()
            ).all()
        ]
    
        return render_template(
            "imei/list.html",
            records=records,
            search=search,
            status=status,
            product_type=product_type,
            category=category,
            supplier_id=supplier_id,
            contact_type=contact_type,
            stock=stock,
            date_from=date_from,
            date_to=date_to,
            counts=counts,
            suppliers=suppliers,
            categories=categories,
            product_summaries=product_summaries,
            source=source,
            product_id=product_id,
        )

    @app.route("/imei-records/<int:record_id>/toggle-status", methods=["POST"])
    @login_required
    def toggle_imei_status(record_id):

        record = db.session.get(IMEIRecord, record_id)

        if not record:
            abort(404)

        try:
            record.is_active = not record.is_active

            db.session.commit()

            message = "activated" if record.is_active else "deactivated"

            flash(
                f"Handset {record.imei1} has been {message}.",
                "success"
            )

        except Exception:
            db.session.rollback()
            app.logger.exception("Handset status update failed")
            flash("Could not update handset status.", "danger")

        return redirect(url_for("imei_records_list"))
    
   
    @app.route("/imei-records/<int:record_id>")
    @login_required
    def view_imei_record(record_id):

        record = db.session.get(IMEIRecord, record_id)

        if not record:
            abort(404)

        return render_template(
                "imei/detail.html",
                    record=record,
         )

    
    @app.route("/imei-records/<int:record_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_imei_record(record_id):

        record = db.session.get(IMEIRecord, record_id)

        if not record:
            abort(404)

        # Only in-stock handsets can be edited
        if record.status != "in_stock":
            flash("Only in-stock handsets can be edited.", "warning")
            return redirect(url_for("imei_records_list"))

        if request.method == "POST":
            try:
                imei1 = request.form.get("imei1", "").strip()
                imei2 = request.form.get("imei2", "").strip() or None

                if not imei1:
                    raise ValueError("IMEI 1 is required.")

                if len(imei1) != 15 or not imei1.isdigit():
                    raise ValueError("IMEI 1 must contain exactly 15 digits.")

                if imei2 and (len(imei2) != 15 or not imei2.isdigit()):
                    raise ValueError("IMEI 2 must contain exactly 15 digits.")

                # Check duplicate IMEI against other handsets
                duplicate = IMEIRecord.query.filter(
                    IMEIRecord.id != record.id,
                    db.or_(
                        IMEIRecord.imei1.in_(
                            [value for value in [imei1, imei2] if value]
                        ),
                        IMEIRecord.imei2.in_(
                            [value for value in [imei1, imei2] if value]
                        )
                    )
                ).first()

                if duplicate:
                    raise ValueError("This IMEI number already exists.")

                if imei2 and imei1 == imei2:
                    raise ValueError("IMEI 1 and IMEI 2 cannot be the same.")

                purchase_price = float(request.form.get("purchase_price", ""))
                selling_price = float(request.form.get("selling_price", ""))
                gst_percent = float(request.form.get("gst_percent", ""))
                warranty_months = int(request.form.get("warranty_months", ""))

                if not all(map(lambda value: value >= 0, [
                    purchase_price,
                    selling_price,
                    gst_percent,
                    warranty_months
                ])):
                    raise ValueError("Prices, GST and warranty cannot be negative.")

                if gst_percent > 100:
                    raise ValueError("GST cannot exceed 100%.")

                condition = request.form.get("condition", "").strip()

                if condition not in ("Sealed", "Used"):
                    raise ValueError("Select a valid handset condition.")

                record.imei1 = imei1
                record.imei2 = imei2
                record.variant = request.form.get("variant", "").strip() or None
                record.color = request.form.get("color", "").strip() or None
                record.purchase_price = purchase_price
                record.selling_price = selling_price
                record.gst_percent = gst_percent
                record.warranty_months = warranty_months
                record.condition = condition
                record.used_grade = (
                    request.form.get("used_grade", "").strip() or None
                    if condition == "Used" else None
                )

                db.session.commit()

                flash("Handset details updated successfully.", "success")
                return redirect(url_for("view_imei_record", record_id=record.id))

            except ValueError as e:
                db.session.rollback()
                flash(str(e), "danger")

            except Exception:
                db.session.rollback()
                app.logger.exception("Error updating handset record")
                flash("Unable to update handset details.", "danger")

        return render_template(
            "imei/edit.html",
            record=record
        )

    # --------------------------------------------------
    # BILLING: LIST AND SEARCH
    # --------------------------------------------------

    @app.route("/billing")
    @login_required
    def billing_list():
        search = request.args.get("search", "").strip()
        query = Sale.query

        if search:
            pattern = f"%{search}%"
            query = query.outerjoin(Customer).filter(
                or_(
                    Sale.invoice_number.ilike(pattern),
                    Customer.name.ilike(pattern),
                    Customer.phone.ilike(pattern),
                )
            )

        sales = query.order_by(Sale.id.desc()).all()

        payment_totals = {}
        for sale in sales:
            payment_totals[sale.id] = sum(
                float(payment.amount or 0)
                for payment in Payment.query.filter_by(sale_id=sale.id).all()
            )

        return render_template(
            "billing/list.html",
            sales=sales,
            search=search,
            payment_totals=payment_totals,
        )
    
    # --------------------------------------------------
    # BILLING: CREATE INVOICE
    # --------------------------------------------------

    @app.route("/billing/create", methods=["GET", "POST"])
    @login_required
    def billing_create():

        # customers = Customer.query.order_by(Customer.name.asc()).all()

        # products = (
        #     Product.query.filter_by(is_active=True)
        #     .order_by(Product.name.asc())
        #     .all()
        # )

        if request.method == "POST":
            # import math

            customer_value = request.form.get("customer_id", "").strip()
            customer_name = request.form.get("customer_name", "").strip()
            customer_phone = request.form.get("customer_phone", "").strip()
            customer_address = request.form.get("customer_address", "").strip()

            discount_text = request.form.get("discount_amount", "0").strip()
            paid_text = request.form.get("paid_amount", "0").strip()
            payment_method = (
                request.form.get("payment_method", "cash").strip().lower()
            )
            reference = request.form.get("payment_reference", "").strip()
            gst_mode = request.form.get("gst_mode", "exclusive").strip().lower()

            allowed_methods = {
                "cash",
                "upi",
                "card",
                "bank_transfer",
                "other",
            }

            allowed_gst_modes = {
                "exclusive",
                "inclusive",
                "exempt",
            }

            try:
                customer_id = int(customer_value) if customer_value else None
                discount = float(discount_text or 0)
                paid_amount = float(paid_text or 0)

                if not math.isfinite(discount) or not math.isfinite(paid_amount):
                    raise ValueError(
                        "Enter valid discount and payment amounts."
                    )


                if customer_id is not None:
                    selected_customer = db.session.get(Customer, customer_id)

                    if selected_customer is None:
                        raise ValueError("Selected customer was not found.")

                elif customer_name:
                    if len(customer_name) > 150:
                        raise ValueError("Customer name cannot exceed 150 characters.")

                    # Avoid creating duplicate customers with different capitalization.
                    normalized_name = " ".join(customer_name.casefold().split())
                    existing_customers = Customer.query.all()
                    existing_customer = next(
                        (
                            customer
                            for customer in existing_customers
                            if " ".join(customer.name.casefold().split()) == normalized_name
                        ),
                        None,
                    )

                    if existing_customer is not None:
                        customer_id = existing_customer.id

                        if customer_phone:
                            existing_customer.phone = customer_phone

                        if customer_address:
                            existing_customer.address = customer_address

                    else:
                        new_customer = Customer(
                            name=customer_name,
                            phone=customer_phone or None,
                            address=customer_address or None,
                        )

                        db.session.add(new_customer)
                        db.session.flush()
                        customer_id = new_customer.id

                if discount < 0 or paid_amount < 0:
                    raise ValueError(
                        "Discount and payment cannot be negative."
                    )

                if customer_id is not None:
                    if db.session.get(Customer, customer_id) is None:
                        raise ValueError("Selected customer was not found.")

                if payment_method not in allowed_methods:
                    raise ValueError("Select a valid payment method.")

                if gst_mode not in allowed_gst_modes:
                    raise ValueError("Select a valid GST calculation mode.")

                product_ids = request.form.getlist("product_id[]")
                quantities = request.form.getlist("quantity[]")

                if not product_ids or len(product_ids) != len(quantities):
                    raise ValueError("Add at least one product to the bill.")

                requested_items = {}

                for product_id_text, quantity_text in zip(
                    product_ids, quantities
                ):
                    if not product_id_text.strip():
                        continue

                    product_id = int(product_id_text)
                    quantity = int(quantity_text)

                    if product_id <= 0:
                        raise ValueError("Invalid product selected.")

                    if quantity <= 0:
                        raise ValueError(
                            "Product quantity must be at least 1."
                        )

                    requested_items[product_id] = (
                        requested_items.get(product_id, 0) + quantity
                    )

                if not requested_items:
                    raise ValueError("Add at least one valid product.")

                selected_products = (
                    Product.query.filter(
                        Product.id.in_(list(requested_items.keys()))
                    )
                    .order_by(Product.id.asc())
                    .with_for_update()
                    .all()
                )

                product_map = {
                    product.id: product for product in selected_products
                }

                subtotal = 0.0
                tax_total = 0.0
                item_rows = []
                imei_records_to_sell = []

                for product_id, quantity in requested_items.items():
                    product = product_map.get(product_id)

                    if product is None or not product.is_active:
                        raise ValueError(
                            "A selected product is unavailable."
                        )

                    available_stock = int(product.stock_quantity or 0)

                    if available_stock < quantity:
                        raise ValueError(
                            f"Insufficient stock for {product.name}. "
                            f"Available: {available_stock}."
                        )

                    # Use the exact IMEI records selected in the billing form.
                    if product.track_imei:
                        imei_field = f"imei_record_ids[{product.id}][]"
                        selected_imei_ids = request.form.getlist(imei_field)

                        if len(selected_imei_ids) != quantity:
                            raise ValueError(
                                f"Select exactly {quantity} IMEI record(s) "
                                f"for {product.name}."
                            )

                        try:
                            selected_imei_ids = [
                                int(record_id) for record_id in selected_imei_ids
                            ]
                        except (ValueError, TypeError):
                            raise ValueError("Invalid IMEI record selection.")

                        if (
                            any(record_id <= 0 for record_id in selected_imei_ids)
                            or len(set(selected_imei_ids)) != quantity
                        ):
                            raise ValueError(
                                f"Invalid or duplicate IMEI selection for {product.name}."
                            )

                        selected_imeis = (
                            IMEIRecord.query
                            .filter(
                                IMEIRecord.id.in_(selected_imei_ids),
                                IMEIRecord.product_id == product.id,
                                IMEIRecord.status == "in_stock",
                                IMEIRecord.sale_id.is_(None),
                            )
                            .with_for_update()
                            .all()
                        )

                        if len(selected_imeis) != quantity:
                            raise ValueError(
                                f"One or more selected IMEI records for {product.name} "
                                "are no longer available. Please refresh and select again."
                            )

                        imei_map = {
                            record.id: record for record in selected_imeis
                        }

                        # Preserve the exact selection from the billing form.
                        imei_records_to_sell.extend(
                            imei_map[record_id]
                            for record_id in selected_imei_ids
                        )


                    # selling_price = float(product.selling_price or 0)
                    # gst_percent = float(product.gst_percent or 0)

                    # Use individual handset prices for IMEI-tracked products.
                    # Calculate the actual total selling amount.
                    if product.track_imei:
                        selling_prices = [
                            float(record.selling_price or 0)
                            for record in selected_imeis
                        ]

                        if any(
                            not math.isfinite(price) or price < 0
                            for price in selling_prices
                        ):
                            raise ValueError(
                                f"Invalid individual handset price for {product.name}."
                            )

                        gross_amount = round(sum(selling_prices), 2)

                    else:
                        selling_price = float(product.selling_price or 0)

                        if not math.isfinite(selling_price) or selling_price < 0:
                            raise ValueError(
                                f"Invalid selling price for {product.name}."
                            )

                        gross_amount = round(selling_price * quantity, 2)

                    
                    if product.track_imei:

                        line_subtotal = 0.0
                        line_tax = 0.0

                        for record in selected_imeis:

                            gst_percent = float(record.gst_percent or 0)
                            handset_gross = float(record.selling_price or 0)

                            if (
                                not math.isfinite(gst_percent)
                                or gst_percent < 0
                                or gst_percent > 100
                            ):
                                raise ValueError(
                                    f"Invalid GST rate for IMEI {record.imei1}."
                                )

                            if gst_mode == "inclusive":

                                if gst_percent > 0:
                                    handset_subtotal = round(
                                        handset_gross / (1 + gst_percent / 100), 2
                                    )
                                else:
                                    handset_subtotal = handset_gross

                                handset_tax = round(
                                    handset_gross - handset_subtotal, 2
                                )

                            elif gst_mode == "exempt":

                                handset_subtotal = handset_gross
                                handset_tax = 0.0

                            else:

                                handset_subtotal = handset_gross
                                handset_tax = round(
                                    handset_subtotal * gst_percent / 100, 2
                                )

                            line_subtotal += handset_subtotal
                            line_tax += handset_tax

                    else:

                        gst_percent = float(product.gst_percent or 0)

                        if (
                            not math.isfinite(gst_percent)
                            or gst_percent < 0
                            or gst_percent > 100
                        ):
                            raise ValueError(
                                f"Invalid GST rate for {product.name}."
                            )

                        if gst_mode == "inclusive":

                            if gst_percent > 0:
                                line_subtotal = round(
                                    gross_amount / (1 + gst_percent / 100), 2
                                )
                            else:
                                line_subtotal = gross_amount

                            line_tax = round(
                                gross_amount - line_subtotal, 2
                            )

                        elif gst_mode == "exempt":

                            line_subtotal = gross_amount
                            line_tax = 0.0

                        else:

                            line_subtotal = gross_amount
                            line_tax = round(
                                line_subtotal * gst_percent / 100, 2
                            )

                    line_subtotal = round(line_subtotal, 2)
                    line_tax = round(line_tax, 2)


                    taxable_unit_price = round(
                        line_subtotal / quantity, 4
                    )

                    subtotal += line_subtotal
                    tax_total += line_tax

                    item_rows.append(
                        {
                            "product": product,
                            "quantity": quantity,
                            "unit_price": taxable_unit_price,
                            "subtotal": line_subtotal,
                            "tax_amount": line_tax,
                        }
                    )

                subtotal = round(subtotal, 2)
                tax_total = round(tax_total, 2)
                gross_total = round(subtotal + tax_total, 2)

                if discount > gross_total:
                    raise ValueError(
                        "Discount cannot exceed the bill total."
                    )

                discount = round(discount, 2)
                grand_total = round(gross_total - discount, 2)

                if paid_amount > grand_total:
                    raise ValueError(
                        "Paid amount cannot exceed the bill total."
                    )

                paid_amount = round(paid_amount, 2)

                invoice_number = (
                    f"INV-{datetime.now().strftime('%Y%m%d%H%M%S')}-"
                    f"{current_user.id}-{os.urandom(2).hex().upper()}"
                )

                sale = Sale(
                    invoice_number=invoice_number,
                    customer_id=customer_id,
                    user_id=current_user.id,
                    subtotal=subtotal,
                    tax_amount=tax_total,
                    discount_amount=discount,
                    gst_mode=gst_mode,
                    total_amount=grand_total,
                    status="completed",
                )

                db.session.add(sale)
                db.session.flush()

                for row in item_rows:
                    product = row["product"]
                    quantity = row["quantity"]

                    db.session.add(
                        SaleItem(
                            sale_id=sale.id,
                            product_id=product.id,
                            quantity=quantity,
                            unit_price=row["unit_price"],
                            tax_amount=row["tax_amount"],
                            subtotal=row["subtotal"],
                        )
                    )

                    product.stock_quantity = (
                        int(product.stock_quantity or 0) - quantity
                    )

                    db.session.add(
                        StockMovement(
                            product_id=product.id,
                            user_id=current_user.id,
                            movement_type="sale",
                            quantity_change=-quantity,
                            reference=invoice_number,
                            notes=(
                                f"Stock deducted for invoice {invoice_number}"
                            ),
                        )
                    )

                
                # # Mark the allocated IMEI records as sold.
                # for imei_record in imei_records_to_sell:
                #     imei_record.status = "sold"
                #     imei_record.sale_id = sale.id
                #     imei_record.sold_date = datetime.now()
                #     imei_record.selling_price = float(
                #             imei_record.product.selling_price or 0
                #         )


                for imei_record in imei_records_to_sell:

                    # Read the optional sales warranty for this individual handset.
                    record_id = str(imei_record.id)

                    warranty_enabled = (
                        request.form.get(f"sale_warranty_enabled[{record_id}]") == "1"
                    )

                    if warranty_enabled:
                        warranty_text = request.form.get(
                            f"sale_warranty_months[{record_id}]", ""
                        ).strip()

                        try:
                            sale_warranty_months = int(warranty_text)
                        except (ValueError, TypeError):
                            raise ValueError(
                                f"Enter a valid warranty duration for IMEI {imei_record.imei1}."
                            )

                        if not 1 <= sale_warranty_months <= 120:
                            raise ValueError(
                                f"Warranty for IMEI {imei_record.imei1} "
                                "must be between 1 and 120 months."
                            )
                    else:
                        sale_warranty_months = 0

                    imei_record.sale_warranty_months = sale_warranty_months
                    imei_record.status = "sold"
                    imei_record.sale_id = sale.id
                    imei_record.sold_date = datetime.now()

                if paid_amount > 0:
                    db.session.add(
                        Payment(
                            sale_id=sale.id,
                            amount=paid_amount,
                            method=payment_method,
                            reference=reference or None,
                            status="completed",
                        )
                    )

                db.session.add(
                    AuditLog(
                        user_id=current_user.id,
                        action="create",
                        entity="Sale",
                        entity_id=sale.id,
                        details=(
                            f"Created invoice {invoice_number}; "
                            f"GST mode {gst_mode}; "
                            f"total {grand_total:.2f}; "
                            f"IMEI units {len(imei_records_to_sell)}"
                        ),
                        ip_address=request.remote_addr,
                    )
                )

                db.session.commit()

                flash(
                    f"Invoice {invoice_number} created successfully.",
                    "success",
                )

                return redirect(
                    url_for("billing_detail", sale_id=sale.id)
                )

            except (ValueError, TypeError, OverflowError) as exc:
                db.session.rollback()
                flash(str(exc), "danger")
                return redirect(url_for("billing_create"))

            except Exception:
                db.session.rollback()
                app.logger.exception("Billing transaction failed")

                flash(
                    "Could not save the bill. No changes were committed.",
                    "danger",
                )

                return redirect(url_for("billing_create"))

        customers = Customer.query.order_by(Customer.name.asc()).all()

        products = (
                Product.query.filter_by(is_active=True)
                .order_by(Product.name.asc())
                .all()
            )

        settings = db.session.get(StoreSettings, 1)

        return render_template(
            "billing/form.html",
            customers=customers,
            products=products,
            settings=settings,
        )
    
    # --------------------------------------------------
    # BILLING: EDIT EXISTING INVOICE
    # --------------------------------------------------

    @app.route(
        "/billing/<int:sale_id>/edit",
        methods=["GET", "POST"],
    )
    @login_required
    def billing_edit(sale_id):
        sale = db.session.get(Sale, sale_id)

        if sale is None:
            flash("Invoice not found.", "danger")
            return redirect(url_for("billing_list"))

        customers = Customer.query.order_by(Customer.name.asc()).all()
        products = (
            Product.query.filter_by(is_active=True).order_by(Product.name.asc()).all()
        )

        if request.method == "POST":
            try:
                customer_value = request.form.get("customer_id", "").strip()
                discount = float(request.form.get("discount_amount", "0") or 0)
                customer_id = int(customer_value) if customer_value else None

                if (
                    customer_id is not None
                    and db.session.get(Customer, customer_id) is None
                ):
                    raise ValueError("Selected customer was not found.")

                if discount < 0:
                    raise ValueError("Discount cannot be negative.")

                product_ids = request.form.getlist("product_id[]")
                quantities = request.form.getlist("quantity[]")

                if not product_ids or len(product_ids) != len(quantities):
                    raise ValueError("Add at least one product to the invoice.")

                requested_items = {}
                for product_id_text, quantity_text in zip(product_ids, quantities):
                    if not product_id_text:
                        continue

                    product_id = int(product_id_text)
                    quantity = int(quantity_text)

                    if quantity <= 0:
                        raise ValueError("Quantity must be at least 1.")

                    requested_items[product_id] = (
                        requested_items.get(product_id, 0) + quantity
                    )

                if not requested_items:
                    raise ValueError("Add at least one valid product.")

                # Lock selected product rows.
                selected_products = (
                    Product.query.filter(Product.id.in_(requested_items.keys()))
                    .order_by(Product.id.asc())
                    .with_for_update()
                    .all()
                )
                product_map = {product.id: product for product in selected_products}

                if len(product_map) != len(requested_items):
                    raise ValueError("One or more selected products were not found.")

                for product in selected_products:
                    if not product.is_active:
                        raise ValueError(
                            f"{product.name} is inactive and cannot be added."
                        )

                old_quantities = {}
                for item in sale.items:
                    old_quantities[item.product_id] = old_quantities.get(
                        item.product_id, 0
                    ) + int(item.quantity or 0)

                subtotal = 0.0
                tax_total = 0.0
                item_rows = []

                for product_id, quantity in requested_items.items():
                    product = product_map[product_id]

                    available_stock = int(
                        product.stock_quantity or 0
                    ) + old_quantities.get(product_id, 0)
                    if available_stock < quantity:
                        raise ValueError(
                            f"Insufficient stock for {product.name}. "
                            f"Available for this edit: {available_stock}."
                        )

                    unit_price = float(product.selling_price or 0)
                    gst_percent = float(product.gst_percent or 0)

                    if unit_price < 0 or gst_percent < 0:
                        raise ValueError(
                            f"Invalid price or GST rate for {product.name}."
                        )

                    line_subtotal = round(unit_price * quantity, 2)
                    line_tax = round(line_subtotal * gst_percent / 100, 2)

                    subtotal += line_subtotal
                    tax_total += line_tax

                    item_rows.append(
                        {
                            "product": product,
                            "quantity": quantity,
                            "unit_price": unit_price,
                            "subtotal": line_subtotal,
                            "tax_amount": line_tax,
                        }
                    )

                subtotal = round(subtotal, 2)
                tax_total = round(tax_total, 2)

                if discount > subtotal + tax_total:
                    raise ValueError("Discount cannot exceed the bill total.")

                grand_total = round(subtotal + tax_total - discount, 2)

                paid_amount = round(
                    sum(
                        float(payment.amount or 0)
                        for payment in Payment.query.filter_by(
                            sale_id=sale.id,
                            status="completed",
                        ).all()
                    ),
                    2,
                )

                if grand_total < paid_amount:
                    raise ValueError(
                        f"The revised total (₹{grand_total:.2f}) is below "
                        f"completed payments (₹{paid_amount:.2f}). "
                        "A refund or customer-credit entry is required first."
                    )

                # Lock all products involved, including removed old items.
                all_product_ids = set(old_quantities) | set(requested_items)
                stock_products = (
                    Product.query.filter(Product.id.in_(all_product_ids))
                    .order_by(Product.id.asc())
                    .with_for_update()
                    .all()
                )
                stock_map = {product.id: product for product in stock_products}

                for product_id in all_product_ids:
                    product = stock_map.get(product_id)
                    if product is None:
                        raise ValueError(
                            "A product on this invoice could not be found."
                        )

                    old_qty = old_quantities.get(product_id, 0)
                    new_qty = requested_items.get(product_id, 0)
                    stock_change = old_qty - new_qty

                    if stock_change:
                        product.stock_quantity = (
                            int(product.stock_quantity or 0) + stock_change
                        )

                        db.session.add(
                            StockMovement(
                                product_id=product_id,
                                user_id=current_user.id,
                                movement_type="sale_adjustment",
                                quantity_change=stock_change,
                                reference=sale.invoice_number,
                                notes=(
                                    f"Invoice {sale.invoice_number} edited. "
                                    f"Previous quantity: {old_qty}; "
                                    f"revised quantity: {new_qty}."
                                ),
                            )
                        )

                # Replace invoice lines; preserve all payment rows.
                for old_item in list(sale.items):
                    db.session.delete(old_item)

                db.session.flush()

                sale.customer_id = customer_id
                sale.subtotal = subtotal
                sale.tax_amount = tax_total
                sale.discount_amount = round(discount, 2)
                sale.total_amount = grand_total

                for row in item_rows:
                    db.session.add(
                        SaleItem(
                            sale_id=sale.id,
                            product_id=row["product"].id,
                            quantity=row["quantity"],
                            unit_price=row["unit_price"],
                            tax_amount=row["tax_amount"],
                            subtotal=row["subtotal"],
                        )
                    )

                db.session.add(
                    AuditLog(
                        user_id=current_user.id,
                        action="update",
                        entity="Sale",
                        entity_id=sale.id,
                        details=(
                            f"Updated invoice {sale.invoice_number}; "
                            f"revised total {grand_total:.2f}"
                        ),
                        ip_address=request.remote_addr,
                    )
                )

                db.session.commit()
                flash(
                    f"Invoice {sale.invoice_number} updated successfully.",
                    "success",
                )
                return redirect(url_for("billing_detail", sale_id=sale.id))

            except (ValueError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                app.logger.exception("Invoice edit failed")
                flash(
                    "Could not update the invoice. " "No changes were committed.",
                    "danger",
                )

            return redirect(url_for("billing_edit", sale_id=sale_id))

        return render_template(
            "billing/edit.html",
            sale=sale,
            customers=customers,
            products=products,
        )
            
    # --------------------------------------------------
    # BILLING: DUE PAYMENTS / COLLECTIONS
    # --------------------------------------------------
    @app.route("/billing/collections")
    @login_required
    def billing_collections():
        sales = Sale.query.order_by(Sale.id.desc()).all()

        collections = []
        total_due = 0.0

        for sale in sales:
            payments = (
                Payment.query
                .filter_by(
                    sale_id=sale.id,
                    status="completed",
                )
                .order_by(Payment.id.desc())
                .all()
            )

            paid = sum(
                float(payment.amount or 0)
                for payment in payments
            )

            total = float(sale.total_amount or 0)
            balance = round(max(total - paid, 0), 2)

            if balance > 0:
                collections.append({
                    "sale": sale,
                    "paid": round(paid, 2),
                    "balance": balance,
                    "payments": payments,
                })

                total_due += balance

        return render_template(
            "billing/collections.html",
            collections=collections,
            total_due=round(total_due, 2),
        )
        
    # --------------------------------------------------
    # BILLING: INVOICE DETAILS
    # --------------------------------------------------

    @app.route("/billing/<int:sale_id>")
    @login_required
    def billing_detail(sale_id):
        sale = db.session.get(Sale, sale_id)

        if sale is None:
            flash("Invoice not found.", "danger")
            return redirect(url_for("billing_list"))

        payments = (
        Payment.query
        .filter_by(sale_id=sale.id)
        .order_by(Payment.id.asc())
        .all()
    )

        paid_amount = round(
        sum(
            float(payment.amount or 0)
            for payment in payments
            if (payment.status or "").lower() == "completed"
        ),
        2,
    )

        balance = round(
        max(
            float(sale.total_amount or 0) - paid_amount,
            0.0,
        ),
        2,
    )

        store = StoreSettings.query.first()

        qr_url = url_for(
        "billing_detail",
        sale_id=sale.id,
        _external=True,
        )

        print("STORE SETTINGS:", store)
        print("INSTAGRAM URL:", store.instagram_url if store else "NO STORE")
        print("FACEBOOK URL:", store.facebook_url if store else "NO STORE")
        print("WHATSAPP URL:", store.whatsapp_url if store else "NO STORE")

        return render_template(
        "billing/detail.html",
        sale=sale,
        store=store,
        payments=payments,
        paid_amount=paid_amount,
        balance=balance,
        qr_url=qr_url,
    )

    
    @app.route("/billing/transactions")
    @login_required
    def billing_transactions():
        payments = (
            Payment.query
            .filter(
                Payment.status == "completed",
                Payment.sale_id.isnot(None),
            )
            .order_by(Payment.id.desc())
            .all()
        )

        total_collected = round(
            sum(float(payment.amount or 0) for payment in payments),
            2,
        )

        return render_template(
            "billing/transactions.html",
            payments=payments,
            total_collected=total_collected,
        )
    
    # --------------------------------------------------
    # BILLING: RECEIVE DUE PAYMENT
    # --------------------------------------------------

    @app.route(
        "/billing/<int:sale_id>/receive-payment",
        methods=["GET", "POST"],
    )
    @login_required
    def billing_receive_payment(sale_id):
        sale = db.session.get(Sale, sale_id)
        if sale is None:
            flash("Invoice not found.", "danger")
            return redirect(url_for("billing_list"))

        def get_paid_amount():
            completed_payments = Payment.query.filter_by(
                sale_id=sale_id,
                status="completed",
            ).all()
            return round(
                sum(float(payment.amount or 0) for payment in completed_payments),
                2,
            )

        paid_amount = get_paid_amount()
        total_amount = round(float(sale.total_amount or 0), 2)
        balance = round(max(total_amount - paid_amount, 0.0), 2)

        if balance <= 0.009:
            flash("This invoice has already been fully paid.", "info")
            return redirect(url_for("billing_detail", sale_id=sale_id))

        if request.method == "POST":
            try:
                amount_text = request.form.get("amount", "").strip()
                method = request.form.get("method", "cash").strip().lower()
                reference = request.form.get("reference", "").strip()

                if not amount_text:
                    raise ValueError("Please enter the payment amount.")

                try:
                    amount = float(amount_text)
                except (TypeError, ValueError, OverflowError):
                    raise ValueError("Enter a valid payment amount.")

                if not math.isfinite(amount) or amount <= 0:
                    raise ValueError("Payment amount must be greater than zero.")

                amount = round(amount, 2)
                if amount <= 0:
                    raise ValueError("Payment amount must be at least ₹0.01.")

                # Keep these options compatible with billing_create().
                allowed_methods = {"cash", "upi", "card", "bank", "credit"}
                if method not in allowed_methods:
                    raise ValueError("Please select a valid payment method.")

                if len(reference) > 100:
                    raise ValueError("Payment reference cannot exceed 100 characters.")

                # Recalculate immediately before recording the payment.
                paid_amount = get_paid_amount()
                balance = round(max(total_amount - paid_amount, 0.0), 2)
                if balance <= 0.009:
                    raise ValueError("This invoice has already been fully paid.")
                if amount > balance + 0.001:
                    raise ValueError(
                        f"Payment cannot exceed the outstanding balance of ₹{balance:.2f}."
                    )

                payment = Payment(
                    sale_id=sale_id,
                    amount=amount,
                    method=method,
                    reference=reference or None,
                    status="completed",
                )
                db.session.add(payment)
                db.session.flush()

                db.session.add(
                    AuditLog(
                        user_id=current_user.id,
                        action="create",
                        entity="Payment",
                        entity_id=payment.id,
                        details=(
                            f"Received ₹{amount:.2f} for invoice "
                            f"{sale.invoice_number} using {method}."
                        ),
                        ip_address=request.remote_addr,
                    )
                )
                db.session.commit()

                flash(
                    f"Payment of ₹{amount:.2f} received successfully.",
                    "success",
                )
                return redirect(url_for("billing_detail", sale_id=sale_id))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
            except Exception:
                db.session.rollback()
                app.logger.exception("Failed to record invoice payment.")
                flash("Could not record the payment. Please try again.", "danger")

            # Refresh values after validation or database errors.
            sale = db.session.get(Sale, sale_id)
            if sale is None:
                flash("Invoice not found.", "danger")
                return redirect(url_for("billing_list"))
            total_amount = round(float(sale.total_amount or 0), 2)
            paid_amount = get_paid_amount()
            balance = round(max(total_amount - paid_amount, 0.0), 2)
            if balance <= 0.009:
                return redirect(url_for("billing_detail", sale_id=sale_id))

        return render_template(
            "billing/receive_payment.html",
            sale=sale,
            paid_amount=paid_amount,
            balance=balance,
        )
        
    # --------------------------------------------------
    # EXPENSES: LIST
    # --------------------------------------------------
    @app.route("/expenses")
    @login_required
    def expenses_list():
        search = request.args.get("search", "").strip()
        period = request.args.get("period", "month").strip().lower()
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()
        today = date.today()
        query = Expense.query
         # Date filter
        if period == "today":
            query = query.filter(
                Expense.expense_date >= datetime.combine(
                    today, datetime.min.time()
                ),
                Expense.expense_date < datetime.combine(
                    today + timedelta(days=1), datetime.min.time()
                )
            )

        elif period == "month":
            month_start = today.replace(day=1)

            if today.month == 12:
                next_month = date(today.year + 1, 1, 1)
            else:
                next_month = date(today.year, today.month + 1, 1)

            query = query.filter(
                Expense.expense_date >= datetime.combine(
                    month_start, datetime.min.time()
                ),
                Expense.expense_date < datetime.combine(
                    next_month, datetime.min.time()
                )
            )

        elif period == "year":
            year_start = date(today.year, 1, 1)
            next_year = date(today.year + 1, 1, 1)

            query = query.filter(
                Expense.expense_date >= datetime.combine(
                    year_start, datetime.min.time()
                ),
                Expense.expense_date < datetime.combine(
                    next_year, datetime.min.time()
                )
            )

        elif period == "custom":
            try:
                if start_date:
                    start = date.fromisoformat(start_date)
                    query = query.filter(
                        Expense.expense_date >= datetime.combine(
                            start, datetime.min.time()
                        )
                    )

                if end_date:
                    end = date.fromisoformat(end_date)
                    query = query.filter(
                        Expense.expense_date < datetime.combine(
                            end + timedelta(days=1), datetime.min.time()
                        )
                    )

                if start_date and end_date and start_date > end_date:
                    flash(
                        "Start date cannot be after end date.",
                        "warning"
                    )
                    return redirect(url_for("expenses_list"))

            except ValueError:
                flash("Please enter valid dates.", "danger")
                return redirect(url_for("expenses_list"))

        # Search by title, category, or notes
        if search:
            query = query.filter(
                db.or_(
                    Expense.title.ilike(f"%{search}%"),
                    Expense.category.ilike(f"%{search}%"),
                    Expense.notes.ilike(f"%{search}%")
                )
            )

        # All dates
        elif period == "all":
            pass

        # Latest expenses first
        expenses = query.order_by(
            Expense.expense_date.desc(),
            Expense.id.desc()
        ).all()

        # Total for filtered expenses
        total_expenses = round(
            sum(float(expense.amount or 0) for expense in expenses),
            2
        )

        return render_template(
            "expenses/list.html",
            expenses=expenses,
            search=search,
            total_expenses=total_expenses,
            period=period,
            start_date=start_date,
            end_date=end_date
        )

    # --------------------------------------------------
    # EXPENSES: CREATE
    # --------------------------------------------------
    @app.route("/expenses/create", methods=["GET", "POST"])
    @login_required
    def expense_create():
        if request.method == "POST":
            try:
                title = request.form.get("title", "").strip()
                category = request.form.get("category", "").strip()
                amount = float(request.form.get("amount", "0"))
                date_text = request.form.get("expense_date", "").strip()
                notes = request.form.get("notes", "").strip()

                if not title:
                    raise ValueError("Expense title is required.")

                if len(title) > 150:
                    raise ValueError("Expense title cannot exceed 150 characters.")

                if not amount > 0 or not math.isfinite(amount):
                    raise ValueError("Enter a valid expense amount greater than zero.")

                expense_date = (
                    datetime.strptime(date_text, "%Y-%m-%d")
                    if date_text
                    else datetime.now()
                )

                expense = Expense(
                    title=title,
                    category=category or None,
                    amount=round(amount, 2),
                    expense_date=expense_date,
                    notes=notes or None,
                    user_id=current_user.id,
                )

                db.session.add(expense)
                db.session.flush()

                db.session.add(
                    AuditLog(
                        user_id=current_user.id,
                        action="create",
                        entity="Expense",
                        entity_id=expense.id,
                        details=(
                            f"Created expense '{title}' "
                            f"for ₹{amount:.2f}."
                        ),
                        ip_address=request.remote_addr,
                    )
                )

                db.session.commit()
                flash("Expense added successfully.", "success")
                return redirect(url_for("expenses_list"))

            except (ValueError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                app.logger.exception("Expense creation failed.")
                flash("Could not save the expense.", "danger")

        return render_template("expenses/form.html", expense=None)

    # --------------------------------------------------
    # EXPENSES: EDIT
    # --------------------------------------------------
    @app.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
    @login_required
    def expense_edit(expense_id):
        expense = db.session.get(Expense, expense_id)

        if expense is None:
            flash("Expense not found.", "danger")
            return redirect(url_for("expenses_list"))

        if request.method == "POST":
            try:
                title = request.form.get("title", "").strip()
                category = request.form.get("category", "").strip()
                amount = float(request.form.get("amount", "0"))
                date_text = request.form.get("expense_date", "").strip()
                notes = request.form.get("notes", "").strip()

                if not title:
                    raise ValueError("Expense title is required.")

                if len(title) > 150:
                    raise ValueError("Expense title cannot exceed 150 characters.")

                if not amount > 0 or not math.isfinite(amount):
                    raise ValueError("Enter a valid expense amount greater than zero.")

                if not date_text:
                    raise ValueError("Expense date is required.")

                expense_date = datetime.strptime(date_text, "%Y-%m-%d")

                expense.title = title
                expense.category = category or None
                expense.amount = round(amount, 2)
                expense.expense_date = expense_date
                expense.notes = notes or None

                db.session.add(
                    AuditLog(
                        user_id=current_user.id,
                        action="update",
                        entity="Expense",
                        entity_id=expense.id,
                        details=f"Updated expense '{title}' to ₹{amount:.2f}.",
                        ip_address=request.remote_addr,
                    )
                )

                db.session.commit()
                flash("Expense updated successfully.", "success")
                return redirect(url_for("expenses_list"))

            except (ValueError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                app.logger.exception("Expense update failed.")
                flash("Could not update the expense.", "danger")

        return render_template("expenses/form.html", expense=expense)

    # --------------------------------------------------
    # EXPENSES: DELETE
    # --------------------------------------------------
    @app.route("/expenses/<int:expense_id>/delete", methods=["POST"])
    @login_required
    @roles_required("admin")
    def expense_delete(expense_id):
        expense = db.session.get(Expense, expense_id)

        if expense is None:
            flash("Expense not found.", "danger")
            return redirect(url_for("expenses_list"))

        try:
            expense_title = expense.title
            expense_amount = float(expense.amount or 0)
            expense_id_value = expense.id

            db.session.add(
                AuditLog(
                    user_id=current_user.id,
                    action="delete",
                    entity="Expense",
                    entity_id=expense_id_value,
                    details=(
                        f"Deleted expense '{expense_title}' "
                        f"for ₹{expense_amount:.2f}."
                    ),
                    ip_address=request.remote_addr,
                )
            )

            db.session.delete(expense)
            db.session.commit()

            flash("Expense deleted successfully.", "success")

        except Exception:
            db.session.rollback()
            app.logger.exception("Expense deletion failed.")
            flash("Could not delete the expense.", "danger")

        return redirect(url_for("expenses_list"))
        
    
    # --------------------------------------------------
    # SERVICES: DASHBOARD / LIST
    # --------------------------------------------------

    @app.route("/services")
    @login_required
    def services_list():
        search = request.args.get("search", "").strip()
        selected_status = request.args.get("status", "").strip()
        allowed_statuses = {
            "Received",
            "Diagnosing",
            "Waiting for Parts",
            "In Progress",
            "Completed",
            "Ready for Pickup",
            "Delivered",
            "Cancelled",
        }

        query = ServiceJob.query

        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(
                    ServiceJob.job_number.ilike(pattern),
                    ServiceJob.customer_name.ilike(pattern),
                    ServiceJob.customer_phone.ilike(pattern),
                    ServiceJob.device_brand.ilike(pattern),
                    ServiceJob.device_model.ilike(pattern),
                    ServiceJob.imei_number.ilike(pattern),
                )
            )

        if selected_status in allowed_statuses:
            query = query.filter(ServiceJob.status == selected_status)
        else:
            selected_status = ""

        jobs = query.order_by(ServiceJob.id.desc()).all()

        print("SERVICE LIST JOBS:", [
        (job.id, job.job_number, job.customer_name)
        for job in jobs
        ])

        counts = {
            "total": ServiceJob.query.count(),
            "received": ServiceJob.query.filter_by(status="Received").count(),
            "in_progress": ServiceJob.query.filter_by(status="In Progress").count(),
            "ready": ServiceJob.query.filter_by(status="Ready for Pickup").count(),
            "completed": ServiceJob.query.filter_by(status="Completed").count(),
        }

        outstanding = sum(
            float(job.balance_due or 0)
            for job in ServiceJob.query.all()
            if job.status != "Cancelled"
        )

        # Total profit from service jobs
        total_service_profit = sum(
            job.service_profit
            for job in ServiceJob.query.filter(
                ServiceJob.status != "Cancelled"
            ).all()
        )

        return render_template(
            "services/list.html",
            jobs=jobs,
            search=search,
            selected_status=selected_status,
            counts=counts,
            outstanding=outstanding,
            total_service_profit=float(total_service_profit or 0),
            statuses=sorted(allowed_statuses),
        )   

    # --------------------------------------------------
    # SERVICES: CREATE JOB
    # --------------------------------------------------

    @app.route("/services/create", methods=["GET", "POST"])
    @login_required
    def service_create():
        customers = Customer.query.order_by(Customer.name.asc()).all()

        service_stocks = ServiceStock.query.filter(
        ServiceStock.available_quantity > 0
        ).order_by(ServiceStock.product_name.asc()).all()

        if request.method == "POST":
            try:
                customer_value = request.form.get("customer_id", "").strip()
                customer_name = request.form.get("customer_name", "").strip()
                customer_id = int(customer_value) if customer_value else None

                customer = (
                    db.session.get(Customer, customer_id)
                    if customer_id else None
                )



                if customer_id and not customer:
                    raise ValueError("Selected customer was not found.")

                customer_name = (
                    customer.name if customer
                    else request.form.get("customer_name", "").strip()
                )
                
                customer_phone = (
                (customer.phone or request.form.get("customer_phone", "").strip())
                if customer
                else request.form.get("customer_phone", "").strip()
                )

                device_brand = request.form.get("device_brand", "").strip()
                device_model = request.form.get("device_model", "").strip()
                service_type = request.form.get("service_type", "Repair").strip()
                imei_number = request.form.get("imei_number", "").strip()
                problem = request.form.get("problem_description", "").strip()
                technician = request.form.get("technician_name", "").strip()
                technician_contact = request.form.get("technician_contact", "").strip()
                priority = request.form.get("priority", "Normal").strip()
                accessories = request.form.get("accessories_received", "").strip()
                estimated_raw = request.form.get("estimated_completion", "").strip()

                allowed_types = {
                    "Repair",
                    "Warranty",
                    "Software Support",
                    "Accessories Fitting",
                    "Maintenance",
                    "Other",
                }
                allowed_priorities = {"Low", "Normal", "High", "Urgent"}

                if service_type not in allowed_types:
                    raise ValueError("Select a valid service type.")

                if priority not in allowed_priorities:
                    raise ValueError("Select a valid priority.")

                if not customer_name or not customer_phone:
                    raise ValueError("Customer name and phone are required.")

                if not device_brand or not device_model:
                    raise ValueError("Device brand and model are required.")

                if not problem:
                    raise ValueError("Describe the customer's problem.")

                if imei_number and (
                    not imei_number.isdigit()
                    or len(imei_number) not in {14, 15, 16}
                ):
                    raise ValueError("Enter a valid device IMEI number.")

                def read_money(field, default=0):
                    raw = request.form.get(field, "").strip()
                    try:
                        value = float(raw) if raw else float(default)
                    except (TypeError, ValueError, OverflowError):
                        raise ValueError(
                            f"Enter a valid {field.replace('_', ' ')}."
                        )

                    if not math.isfinite(value) or value < 0:
                        raise ValueError(
                            f"{field.replace('_', ' ').title()} "
                            "cannot be negative."
                        )
                    return round(value, 2)

                service_charge = read_money("service_charge")
                
                technician_cost = read_money("technician_cost")
                other_expenses = read_money("other_expenses")

                # Read selected spare parts from the service form
                selected_stock_ids = request.form.getlist("service_stock_id")
                selected_quantities = request.form.getlist("part_quantity")

                selected_parts = []
                parts_charge = Decimal("0.00")

                if len(selected_stock_ids) != len(selected_quantities):
                    raise ValueError("Invalid spare parts selection.")

                for stock_id_raw, quantity_raw in zip(
                    selected_stock_ids,
                    selected_quantities
                ):
                    if not stock_id_raw:
                        continue

                    try:
                        stock_id = int(stock_id_raw)
                        quantity = int(quantity_raw)
                    except (ValueError, TypeError):
                        raise ValueError("Select a valid spare part and quantity.")

                    if quantity <= 0:
                        raise ValueError("Part quantity must be greater than zero.")

                    stock = db.session.get(ServiceStock, stock_id)

                    if stock is None:
                        raise ValueError("Selected spare part was not found.")

                    if quantity > stock.available_quantity:
                        raise ValueError(
                            f"Only {stock.available_quantity} units of "
                            f"{stock.product_name} are available."
                        )

                    unit_price = Decimal(str(stock.selling_price or 0))
                    unit_cost = Decimal(str(stock.cost_price or 0))
                    subtotal = unit_price * quantity

                    selected_parts.append({
                        "stock": stock,
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "unit_cost": unit_cost,
                        "subtotal": subtotal,
                    })

                    parts_charge += subtotal

                parts_charge = round(float(parts_charge), 2)

                discount_amount = read_money("discount_amount")
                tax_amount = read_money("tax_amount")

                before_discount = service_charge + parts_charge + tax_amount

                if discount_amount > before_discount:
                    raise ValueError(
                        "Discount cannot exceed the service total."
                    )

                total_amount = round(
                    before_discount - discount_amount, 2
                )

                paid_amount = read_money("amount_paid")

                if paid_amount > total_amount:
                    raise ValueError(
                        "Initial payment cannot exceed the total amount."
                    )

                estimated_completion = None
                if estimated_raw:
                    try:
                        estimated_completion = datetime.strptime(
                            estimated_raw, "%Y-%m-%dT%H:%M"
                        )
                    except ValueError:
                        raise ValueError(
                            "Enter a valid estimated completion date and time."
                        )

                job_number = (
                    f"SRV-{datetime.now().strftime('%Y%m%d%H%M%S')}-"
                    f"{os.urandom(2).hex().upper()}"
                )

                job = ServiceJob(
                    job_number=job_number,
                    service_type=service_type,
                    device_brand=device_brand,
                    device_model=device_model,
                    imei_number=imei_number or None,
                    customer_id=customer.id if customer else None,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                    problem_description=problem,
                    diagnosis=request.form.get("diagnosis", "").strip() or None,
                    solution=request.form.get("solution", "").strip() or None,
                    accessories_received=accessories or None,
                    technician_name=technician or None,
                    technician_contact=technician_contact or None,
                    priority=priority,
                    status="Received",
                    estimated_completion=estimated_completion,
                    service_charge=service_charge,
                    parts_charge=parts_charge,
                    discount_amount=discount_amount,
                    tax_amount=tax_amount,
                    total_amount=total_amount,
                    technician_cost=technician_cost,
                    other_expenses=other_expenses,
                    amount_paid=paid_amount,
                    payment_method=(
                        request.form.get("payment_method", "cash").strip().lower()
                        if paid_amount > 0 else None
                    ),
                    warranty_days=0,
                    internal_notes=request.form.get("internal_notes", "").strip() or None,
                    customer_notes=request.form.get("customer_notes", "").strip() or None,
                    created_by=current_user.id,
                )

                job.update_payment_status()

                db.session.add(job)
                db.session.flush()

                # Save selected parts and deduct stock
                for item in selected_parts:
                    stock = item["stock"]
                    quantity = item["quantity"]

                    service_part = ServicePart(
                        service_job_id=job.id,
                        service_stock_id=stock.id,
                        part_name=stock.product_name,
                        quantity=quantity,
                        unit_cost=item["unit_cost"],
                        unit_price=item["unit_price"],
                        subtotal=item["subtotal"],
                    )

                    db.session.add(service_part)

                    stock.available_quantity -= quantity

                db.session.add(
                    ServiceHistory(
                        service_job_id=job.id,
                        old_status=None,
                        new_status="Received",
                        remarks="Service job created.",
                        updated_by=current_user.id,
                    )
                )

                if paid_amount > 0:
                    allowed_methods = {
                        "cash", "upi", "card", "bank", "credit"
                    }
                    method = (
                        request.form.get("payment_method", "cash")
                        .strip()
                        .lower()
                    )
                    if method not in allowed_methods:
                        raise ValueError("Select a valid payment method.")

                db.session.add(
                    AuditLog(
                        user_id=current_user.id,
                        action="create",
                        entity="ServiceJob",
                        entity_id=job.id,
                        details=(
                            f"Created service job {job_number}; "
                            f"total {total_amount:.2f}"
                        ),
                        ip_address=request.remote_addr,
                    )
                )

                db.session.commit()
                flash(f"Service job {job_number} created successfully.", "success")
                return redirect(url_for("service_detail", service_id=job.id))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Service job creation failed")
                flash("Could not create the service job.", "danger")

        return render_template(
            "services/form.html",
            customers=customers,
            job=None,
            service_stocks=service_stocks,
        )

    @app.route("/services/<int:service_id>")
    @login_required
    def service_detail(service_id):
        job = db.session.get(ServiceJob, service_id)

        if job is None:
            flash("Service job not found.", "warning")
            return redirect(url_for("services_list"))

        statuses = [
            "Received",
            "Diagnosing",
            "Waiting for Parts",
            "In Progress",
            "Completed",
            "Ready for Pickup",
            "Delivered",
            "Cancelled",
        ]

        # Get available service stock
        service_stocks = ServiceStock.query.filter(
            ServiceStock.available_quantity > 0
        ).order_by(ServiceStock.product_name).all()

        return render_template(
            "services/detail.html",
            job=job,
            statuses=statuses,
            service_stocks=service_stocks,
        )

    # --------------------------------------------------
    # SERVICES: UPDATE STATUS
    # --------------------------------------------------

    @app.route("/services/<int:service_id>/add-part", methods=["POST"])
    @login_required
    def service_add_part(service_id):
        job = db.session.get(ServiceJob, service_id)

        if job is None:
            flash("Service job not found.", "warning")
            return redirect(url_for("services_list"))

        if job.status in ["Delivered", "Cancelled"]:
            flash("Parts cannot be added to a closed service job.", "warning")
            return redirect(url_for("service_detail", service_id=job.id))

        try:
            service_stock_id = int(request.form.get("service_stock_id", 0))
            quantity = int(request.form.get("quantity", 0))

            if quantity <= 0:
                flash("Quantity must be greater than zero.", "warning")
                return redirect(url_for("service_detail", service_id=job.id))

            # Get the selected spare part
            stock = db.session.get(ServiceStock, service_stock_id)

            if stock is None:
                flash("Selected spare part was not found.", "warning")
                return redirect(url_for("service_detail", service_id=job.id))

            # Check available quantity
            if quantity > stock.available_quantity:
                flash(
                    f"Only {stock.available_quantity} units are available.",
                    "warning"
                )
                return redirect(url_for("service_detail", service_id=job.id))

            # Save cost price and selling price
            unit_cost = Decimal(str(stock.cost_price or 0))
            unit_price = Decimal(str(stock.selling_price or 0))
            subtotal = unit_price * quantity

            # Create service part record
            service_part = ServicePart(
                service_job_id=job.id,
                service_stock_id=stock.id,
                part_name=stock.product_name,
                quantity=quantity,
                unit_cost=unit_cost,
                unit_price=unit_price,
                subtotal=subtotal
            )

            db.session.add(service_part)

            # Reduce available stock
            stock.available_quantity -= quantity

                       # Recalculate parts selling amount from all parts used
            db.session.flush()

            parts_total = sum(
                (
                    Decimal(str(part.subtotal or 0))
                    for part in job.parts
                ),
                Decimal("0.00")
            )

            job.parts_charge = parts_total

            # Recalculate total service bill
            job.total_amount = (
                Decimal(str(job.service_charge or 0))
                + parts_total
                + Decimal(str(job.tax_amount or 0))
                - Decimal(str(job.discount_amount or 0))
            )

            # Update payment status
            job.update_payment_status()
            
            # Update payment status if your model provides this method
            if hasattr(job, "update_payment_status"):
                job.update_payment_status()

            db.session.commit()

            flash("Spare part added to service job successfully.", "success")

        except (ValueError, InvalidOperation):
            db.session.rollback()
            flash("Please enter a valid part and quantity.", "danger")

        except Exception as error:
            db.session.rollback()
            flash(f"Could not add spare part: {error}", "danger")

        return redirect(url_for("service_detail", service_id=job.id))
    

    # --------------------------------------------------
    # SERVICES: UPDATE STATUS
    # --------------------------------------------------

    @app.route(
        "/services/<int:service_id>/status",
        methods=["POST"],
    )
    @login_required
    def service_update_status(service_id):
        job = db.session.get(ServiceJob, service_id)

        if job is None:
            flash("Service job not found.", "warning")
            return redirect(url_for("services_list"))

        allowed_statuses = {
            "Received",
            "Diagnosing",
            "Waiting for Parts",
            "In Progress",
            "Completed",
            "Ready for Pickup",
            "Delivered",
            "Cancelled",
        }

        new_status = request.form.get("status", "").strip()
        remarks = request.form.get("remarks", "").strip()

        if new_status not in allowed_statuses:
            flash("Select a valid service status.", "danger")
            return redirect(url_for("service_detail", service_id=job.id))

        if job.status == "Delivered" and new_status != "Delivered":
            flash("A delivered service job cannot be reopened here.", "warning")
            return redirect(url_for("service_detail", service_id=job.id))

        if job.status == "Cancelled" and new_status != "Cancelled":
            flash("A cancelled job cannot be reopened here.", "warning")
            return redirect(url_for("service_detail", service_id=job.id))

        if new_status == job.status:
            flash("The service already has that status.", "info")
            return redirect(url_for("service_detail", service_id=job.id))

        try:
            old_status = job.status
            job.status = new_status

            if new_status == "Completed" and not job.completed_date:
                job.completed_date = datetime.now()

            if new_status == "Delivered":
                job.delivered_date = datetime.now()

            db.session.add(
                ServiceHistory(
                    service_job_id=job.id,
                    old_status=old_status,
                    new_status=new_status,
                    remarks=remarks or None,
                    updated_by=current_user.id,
                )
            )

            db.session.add(
                AuditLog(
                    user_id=current_user.id,
                    action="update",
                    entity="ServiceJob",
                    entity_id=job.id,
                    details=(
                        f"Service {job.job_number}: "
                        f"{old_status} -> {new_status}"
                    ),
                    ip_address=request.remote_addr,
                )
            )

            db.session.commit()
            flash("Service status updated successfully.", "success")

        except Exception:
            db.session.rollback()
            current_app.logger.exception("Service status update failed")
            flash("Could not update the service status.", "danger")

        return redirect(url_for("service_detail", service_id=job.id))

    # --------------------------------------------------
    # SERVICES: RECEIVE PAYMENT
    # --------------------------------------------------

    @app.route(
        "/services/<int:service_id>/payment",
        methods=["POST"],
    )
    @login_required
    def service_receive_payment(service_id):
        job = db.session.get(ServiceJob, service_id)

        if job is None:
            flash("Service job not found.", "warning")
            return redirect(url_for("services_list"))

        try:
            amount_text = request.form.get("amount", "").strip()
            method = request.form.get("method", "cash").strip().lower()
            reference = request.form.get("reference", "").strip()

            if not amount_text:
                raise ValueError("Enter the payment amount.")

            try:
                amount = float(amount_text)
            except (ValueError, TypeError, OverflowError):
                raise ValueError("Enter a valid payment amount.")

            if not math.isfinite(amount) or amount <= 0:
                raise ValueError("Payment must be greater than zero.")

            amount = round(amount, 2)
            balance = round(float(job.balance_due or 0), 2)

            if balance <= 0:
                raise ValueError("This service job is already fully paid.")

            if amount > balance:
                raise ValueError(
                    f"Payment cannot exceed the balance of ₹{balance:.2f}."
                )

            if method not in {"cash", "upi", "card", "bank", "credit"}:
                raise ValueError("Select a valid payment method.")

            if len(reference) > 100:
                raise ValueError("Reference cannot exceed 100 characters.")

            job.amount_paid = round(
                float(job.amount_paid or 0) + amount, 2
            )
            job.payment_method = method
            job.update_payment_status()

            db.session.add(
                AuditLog(
                    user_id=current_user.id,
                    action="payment",
                    entity="ServiceJob",
                    entity_id=job.id,
                    details=(
                        f"Received ₹{amount:.2f} for {job.job_number}; "
                        f"method {method}; reference {reference or '-'}"
                    ),
                    ip_address=request.remote_addr,
                )
            )

            db.session.commit()
            flash(f"Payment of ₹{amount:.2f} recorded successfully.", "success")

        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Service payment failed")
            flash("Could not record the service payment.", "danger")

        return redirect(url_for("service_detail", service_id=service_id))
    
    # --------------------------------------------------
    # HOME
    # --------------------------------------------------

    @app.route("/")
    def home():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard_page"))
        return redirect(url_for("loading_page"))

    @app.route("/loading")
    def loading_page():
        return render_template("loading.html")

    # --------------------------------------------------
    # LOGIN
    # --------------------------------------------------

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard_page"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")

            user = User.query.filter_by(username=username).first()

            if user and user.is_active and user.check_password(password):
                login_user(user)
                flash("Welcome back!", "success")
                return redirect(url_for("dashboard_page"))

            flash("Invalid username or password.", "danger")

        return render_template("login.html")

    # --------------------------------------------------
    # PROFILE SETTINGS
    # --------------------------------------------------

    @app.route("/profile-settings", methods=["GET", "POST"])
    @login_required
    def profile_settings():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            full_name = request.form.get("full_name", "").strip()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not username or len(username) > 80:
                flash(
                    "Enter a valid username (up to 80 characters).",
                    "danger",
                )
            elif not full_name or len(full_name) > 150:
                flash("Enter your full name.", "danger")
            elif User.query.filter(
                User.username == username,
                User.id != current_user.id,
            ).first():
                flash("That username is already in use.", "danger")
            elif password and len(password) < 2:
                flash(
                    "New password must contain at least 2 characters.",
                    "danger",
                )
            elif password and password != confirm_password:
                flash("Passwords do not match.", "danger")
            else:
                current_user.username = username
                current_user.full_name = full_name

                if password:
                    current_user.set_password(password)

                try:
                    db.session.commit()
                    flash("Profile updated successfully.", "success")
                    return redirect(url_for("profile_settings"))
                except Exception:
                    db.session.rollback()
                    app.logger.exception("Profile update failed")
                    flash("Could not update your profile.", "danger")

        return render_template(
            "profile_settings.html",
            user=current_user,
        )

    # --------------------------------------------------
    # REGISTER: NEW USERS ARE STAFF
    # --------------------------------------------------

    
    @app.route("/register", methods=["GET", "POST"])
    def register_page():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard_page"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            full_name = request.form.get("full_name", "").strip()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")
            role = request.form.get("role", "staff").strip().lower()

            if not username or len(username) > 80:
                flash("Enter a username of up to 80 characters.", "danger")
            elif not full_name or len(full_name) > 150:
                flash("Enter your full name.", "danger")
            elif len(password) < 2:
                flash("Password must contain at least 2 characters.", "danger")
            elif password != confirm_password:
                flash("Passwords do not match.", "danger")
            elif role not in ("admin", "staff"):
                flash("Please select a valid role.", "danger")
            elif User.query.filter_by(username=username).first():
                flash("Username already exists.", "danger")
            else:
                user = User(
                    username=username,
                    full_name=full_name,
                    role=role,
                    is_active=True,
                )
                user.set_password(password)

                try:
                    db.session.add(user)
                    db.session.commit()
                    flash("Registration successful. Please log in.", "success")
                    return redirect(url_for("login_page"))
                except Exception:
                    db.session.rollback()
                    app.logger.exception("Registration failed")
                    flash("Could not create your account.", "danger")

        return render_template("register.html")


    # --------------------------------------------------
    # FIRST ADMIN SETUP
    # --------------------------------------------------



    @app.route("/setup-admin", methods=["GET", "POST"])
    def setup_admin_page():
        admin_exists = User.query.filter_by(role="admin").first()

        if admin_exists:
            flash("An Admin account already exists.", "info")
            return redirect(url_for("login_page"))

        if request.method == "POST":
            username = request.form.get("username", "").strip()
            full_name = request.form.get("full_name", "").strip()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not username or len(username) > 80:
                flash("Enter a valid username.", "danger")
            elif not full_name or len(full_name) > 150:
                flash("Enter your full name.", "danger")
            elif len(password) < 2:
                flash(
                    "Password must contain at least 2 characters.",
                    "danger",
                )
            elif password != confirm_password:
                flash("Passwords do not match.", "danger")
            elif User.query.filter_by(username=username).first():
                flash("Username already exists.", "danger")
            else:
                # Recheck before creating the first admin.
                if User.query.filter_by(role="admin").first():
                    flash("An Admin account already exists.", "danger")
                    return redirect(url_for("login_page"))

                user = User(
                    username=username,
                    full_name=full_name,
                    role="admin",
                    is_active=True,
                )
                user.set_password(password)

                try:
                    db.session.add(user)
                    db.session.commit()
                    flash(
                        "Admin account created. Please log in.",
                        "success",
                    )
                    return redirect(url_for("login_page"))
                except Exception:
                    db.session.rollback()
                    app.logger.exception("Admin setup failed")
                    flash(
                        "Could not create the Admin account.",
                        "danger",
                    )

        return render_template("setup_admin.html")


    # --------------------------------------------------
    # CUSTOMERS: LIST, SEARCH AND DATE FILTERS
    # --------------------------------------------------

    @app.route("/customers")
    @login_required
    def customers_list():
        search = request.args.get("search", "").strip()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()

        query = Customer.query

        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Customer.name.ilike(pattern),
                    Customer.phone.ilike(pattern),
                    Customer.email.ilike(pattern),
                )
            )

        try:
            query = apply_date_filters(query, Customer, date_from, date_to)
        except ValueError:
            flash("Please enter valid filter dates.", "warning")
            return redirect(url_for("customers_list"))

        customers = query.order_by(Customer.id.desc()).all()

        return render_template(
            "customers/list.html",
            customers=customers,
            search=search,
            date_from=date_from,
            date_to=date_to,
            total_customers=Customer.query.count(),
            filtered_count=len(customers),
        )

    # --------------------------------------------------
    # CUSTOMERS: CREATE
    # --------------------------------------------------w

    @app.route("/customers/create", methods=["GET", "POST"])
    @login_required
    def customer_create():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            phone = request.form.get("phone", "").strip()
            email = request.form.get("email", "").strip()
            address = request.form.get("address", "").strip()
            gst_number = request.form.get("gst_number", "").strip()

            if not name:
                flash("Customer name is required.", "danger")
                return render_template("customers/form.html", customer=None)

            customer = Customer(
    customer_code=None,  # Assigned safely after saving
    name=name,
    phone=phone or None,
    email=email or None,
    address=address or None,
    gst_number=gst_number or None,
)

            try:
                db.session.add(customer)
                db.session.flush()
                customer.customer_code = f"CUST-{customer.id:06d}"
                db.session.commit()
                flash("Customer created successfully.", "success")
                return redirect(url_for("customers_list"))
            except Exception:
                db.session.rollback()
                app.logger.exception("Customer creation failed")
                flash("Could not save the customer.", "danger")

        return render_template("customers/form.html", customer=None)

    # --------------------------------------------------
    # CUSTOMERS: EDIT
    # --------------------------------------------------

    @app.route(
        "/customers/<int:customer_id>/edit",
        methods=["GET", "POST"],
    )
    @login_required
    def customer_edit(customer_id):
        customer = db.session.get(Customer, customer_id)

        if customer is None:
            flash("Customer not found.", "danger")
            return redirect(url_for("customers_list"))

        if request.method == "POST":
            name = request.form.get("name", "").strip()

            if not name:
                flash("Customer name is required.", "danger")
                return render_template("customers/form.html", customer=customer)

            customer.name = name
            customer.phone = request.form.get("phone", "").strip() or None
            customer.email = request.form.get("email", "").strip() or None
            customer.address = request.form.get("address", "").strip() or None
            customer.gst_number = request.form.get("gst_number", "").strip() or None

            try:
                db.session.commit()
                flash("Customer updated successfully.", "success")
                return redirect(url_for("customers_list"))
            except Exception:
                db.session.rollback()
                app.logger.exception("Customer update failed")
                flash("Could not update the customer.", "danger")

        return render_template("customers/form.html", customer=customer)

    # --------------------------------------------------
    # PRODUCT LIST
    # --------------------------------------------------
    @app.route("/products")
    @login_required
    def products_list():
        search = request.args.get("search", "").strip()

        status = request.args.get("status", "active").strip().lower()

        if status not in ("active", "inactive", "all"):
            status = "active"

        product_type = (
            request.args.get("product_type")
            or request.args.get("type")
            or ""
        ).strip().title()

        category = request.args.get("category", "").strip()
        supplier_id = request.args.get("supplier_id", type=int)

        contact_type = request.args.get(
            "contact_type", "all"
        ).strip().lower()

        if contact_type not in ("all", "supplier", "seller"):
            contact_type = "all"

        stock = request.args.get("stock", "").strip().lower()
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()

        # Start the product query and show only active products by default.
        query = Product.query

        # Count available individual handsets for IMEI-tracked products.
        imei_stock_subquery = (
            db.session.query(db.func.count(IMEIRecord.id))
            .filter(
                IMEIRecord.product_id == Product.id,
                IMEIRecord.status == "in_stock",
            )
            .correlate(Product)
            .scalar_subquery()
        )

        # Use IMEI stock for tracked products and normal stock otherwise.
        effective_stock = db.case(
            (
                Product.track_imei.is_(True),
                db.func.coalesce(imei_stock_subquery, 0),
            ),
            else_=db.func.coalesce(Product.stock_quantity, 0),
        )

        if status == "active":
            query = query.filter(Product.is_active.is_(True))
        elif status == "inactive":
            query = query.filter(Product.is_active.is_(False))

            # Filter by product condition: Sealed or Used
        if product_type in ("Sealed", "Used"):
            query = query.filter(
            db.func.lower(
                db.func.trim(Product.condition)
            ) == product_type.lower()
        )
        else:
            product_type = ""

        # Search by code, barcode, name, brand, model, or category
       
# Search by product details or supplier name
        if search:
            pattern = f"%{search}%"

            query = query.filter(
                or_(
                    Product.product_code.ilike(pattern),
                    Product.barcode.ilike(pattern),
                    Product.name.ilike(pattern),
                    Product.brand.ilike(pattern),
                    Product.model.ilike(pattern),
                    Product.category.ilike(pattern),

                    # Search direct stock supplier
                    Product.id.in_(
                        db.session.query(IMEIRecord.product_id)
                        .join(
                            Supplier,
                            IMEIRecord.supplier_id == Supplier.id
                        )
                        .filter(
                            or_(
                                Supplier.name.ilike(pattern),
                                Supplier.company_name.ilike(pattern)
                            )
                        )
                    ),

                    # Search purchase supplier
                    Product.id.in_(
                        db.session.query(PurchaseItem.product_id)
                        .join(
                            Purchase,
                            PurchaseItem.purchase_id == Purchase.id
                        )
                        .join(
                            Supplier,
                            Purchase.supplier_id == Supplier.id
                        )
                        .filter(
                            or_(
                                Supplier.name.ilike(pattern),
                                Supplier.company_name.ilike(pattern)
                            )
                        )
                    )
                )
            )


        # Filter by category
        if category:
            query = query.filter(
                Product.category == category
            )

            # Supplier filter: direct stock OR purchase stock
        if supplier_id:
            query = query.filter(
                db.or_(
                    Product.imei_records.any(
                        IMEIRecord.supplier_id == supplier_id
                    ),
                    Product.id.in_(
                        db.session.query(PurchaseItem.product_id)
                        .join(
                            Purchase,
                            PurchaseItem.purchase_id == Purchase.id
                        )
                        .filter(Purchase.supplier_id == supplier_id)
                    )
                )
            )

        # Filter by contact type: Supplier or Individual Seller
        if contact_type == "supplier":
            query = query.filter(
                db.or_(
                    Product.imei_records.any(
                        IMEIRecord.supplier_id.isnot(None)
                    ),
                    Product.id.in_(
                        db.session.query(PurchaseItem.product_id)
                        .join(
                            Purchase,
                            PurchaseItem.purchase_id == Purchase.id,
                        )
                    )
                )
            )
    

        elif contact_type == "seller":
            query = query.filter(
                Product.imei_records.any(
            IMEIRecord.used_handset_seller.has()
            )
        )

        # Filter by stock status
        if stock == "in":
            query = query.filter(
                effective_stock > db.func.coalesce(Product.reorder_level, 5)
            )

        elif stock == "low":
            query = query.filter(
                effective_stock > 0,
                effective_stock <= db.func.coalesce(Product.reorder_level, 5)
            )

        elif stock == "out":
            query = query.filter(effective_stock <= 0)

        else:
            stock = ""

        # Apply date filters
        try:
            query = apply_date_filters(
                query,
                Product,
                date_from,
                date_to,
            )
        except ValueError:
            flash(
                "Please enter valid filter dates.",
                "warning",
            )
            return redirect(url_for("products_list"))

        # Fetch filtered products
        # Fetch filtered products
        products = query.order_by(
            Product.id.desc()
        ).all()

        # Fetch supplier details for each product
        # Fetch supplier details for each product

        product_suppliers = {}

        for product in products:

            # Get only supplier-linked IMEI records
            direct_supplier_ids = (
                db.session.query(IMEIRecord.supplier_id)
                .filter(
                    IMEIRecord.product_id == product.id,
                    IMEIRecord.supplier_id.isnot(None),
                    IMEIRecord.source == "supplier"
                )
        )

            # Get suppliers linked through normal purchase records
            purchase_supplier_ids = (
                db.session.query(Purchase.supplier_id)
                .join(
                    PurchaseItem,
                    PurchaseItem.purchase_id == Purchase.id
                )
                .filter(
                    PurchaseItem.product_id == product.id,
                    Purchase.supplier_id.isnot(None)
                )
        )

            supplier_query = Supplier.query.filter(
                db.or_(
                    Supplier.id.in_(direct_supplier_ids),
                    Supplier.id.in_(purchase_supplier_ids)
                )
            )

            if search:
                matching_suppliers = supplier_query.filter(
                    db.or_(
                        Supplier.name.ilike(pattern),
                        Supplier.company_name.ilike(pattern)
                    )
                ).all()

                supplier_records = (
                    matching_suppliers
                    if matching_suppliers
                    else supplier_query.all()
                )
            else:
                supplier_records = supplier_query.all()

            product_suppliers[product.id] = [
                {
                    "supplier": supplier,
                    "quantity": (
                        db.session.query(db.func.count(IMEIRecord.id))
                        .filter(
                            IMEIRecord.product_id == product.id,
                            IMEIRecord.supplier_id == supplier.id,
                            IMEIRecord.source == "supplier",
                            IMEIRecord.status == "in_stock"
                        )
                        .scalar() or 0
                    )
                }
                for supplier in supplier_records
            ]
        
        # Fetch categories for the dropdown
        categories = [
            row[0]
            for row in (
                db.session.query(Product.category)
                .filter(Product.category.isnot(None))
                .filter(Product.category != "")
                .distinct()
                .order_by(Product.category)
                .all()
            )
        ]
       
        # Overall inventory totals
        # Overall inventory totals
        total_products = Product.query.count()

        active_products = Product.query.filter_by(
            is_active=True
        ).all()

# Calculate actual available stock for each active product.
        product_stock = {}

        for product in active_products:
            if product.track_imei:
                product_stock[product.id] = (
                    db.session.query(db.func.count(IMEIRecord.id))
                    .filter(
                        IMEIRecord.product_id == product.id,
                        IMEIRecord.status == "in_stock"
                    )
                    .scalar() or 0
                )
            else:
                product_stock[product.id] = product.stock_quantity or 0

# Count low-stock products
        low_stock_count = sum(
            1
            for product in active_products
            if 0 < product_stock[product.id]
            <= (
                product.reorder_level
                if product.reorder_level is not None
                else 5
            )
        )

# Count out-of-stock products
        out_stock_count = sum(
            1
            for product in active_products
            if product_stock[product.id] <= 0
        )

        suppliers = (
            Supplier.query
            .filter_by(is_active=True)
            .order_by(Supplier.name.asc())
            .all()
        )

        # Render products page
        return render_template(
            "products/list.html",
            product_stock=product_stock,
            products=products,
            product_suppliers=product_suppliers,
            search=search,
            product_type=product_type,
            category=category,
            stock=stock,
            status=status,
            categories=categories,
            supplier_id=supplier_id,
            suppliers=suppliers,
            date_from=date_from,
            date_to=date_to,
            total_products=total_products,
            filtered_count=len(products),
            low_stock_count=low_stock_count,
            out_stock_count=out_stock_count,
            contact_type=contact_type,
        )


    @app.route("/products/<int:product_id>/handseets")
    @login_required
    def product_handset_details(product_id):

        print("HANDSEETS PAGE REQUEST RECEIVED")
        print("PRODUCT ID:", product_id)
        
        product = db.session.get(Product, product_id)


        product = db.session.get(Product, product_id)

        if not product:
            abort(404)

        # Base handset query for this product
        query = IMEIRecord.query.filter(
            IMEIRecord.product_id == product.id
        )

        # Variant filter
        selected_variant = request.args.get("variant", "").strip()

        if selected_variant:
            query = query.filter(
                IMEIRecord.variant == selected_variant
            )

        # Colour filter
        selected_color = request.args.get("color", "").strip()

        if selected_color:
            query = query.filter(
                IMEIRecord.color == selected_color
            )

        # Fetch filtered handset records
        records = query.order_by(
            IMEIRecord.id.desc()
        ).all()

        # Total counts for the complete product
        total_count = IMEIRecord.query.filter_by(
            product_id=product.id
        ).count()

        available_count = IMEIRecord.query.filter_by(
            product_id=product.id,
            status="in_stock"
        ).count()

        sold_count = IMEIRecord.query.filter_by(
            product_id=product.id,
            status="sold"
        ).count()

        # Available variants
        variants = [
            row[0]
            for row in (
                db.session.query(IMEIRecord.variant)
                .filter(
                    IMEIRecord.product_id == product.id,
                    IMEIRecord.variant.isnot(None),
                    IMEIRecord.variant != ""
                )
                .distinct()
                .order_by(IMEIRecord.variant)
                .all()
            )
        ]

        # Available colours
        colors = [
            row[0]
            for row in (
                db.session.query(IMEIRecord.color)
                .filter(
                    IMEIRecord.product_id == product.id,
                    IMEIRecord.color.isnot(None),
                    IMEIRecord.color != ""
                )
                .distinct()
                .order_by(IMEIRecord.color)
                .all()
            )
        ]

        return render_template(
            "products/handseets.html",
            product=product,
            records=records,
            total_count=total_count,
            available_count=available_count,
            sold_count=sold_count,
            variants=variants,
            colors=colors,
            selected_variant=selected_variant,
            selected_color=selected_color
        )

    
    
    # --------------------------------------------------
    # DASHBOARD
    # --------------------------------------------------
    
    @app.route("/dashboard")
    @login_required
    def dashboard_page():
        today = datetime.now().date()
        today_start = datetime.combine(today, datetime.min.time())
        tomorrow_start = today_start + timedelta(days=1)

        # Today's sales value
        today_sales = (
            db.session.query(
                func.coalesce(func.sum(Sale.total_amount), 0)
            )
            .filter(
                Sale.sale_date >= today_start,
                Sale.sale_date < tomorrow_start,
            )
            .scalar()
        )

        # Number of orders today
        today_orders = Sale.query.filter(
            Sale.sale_date >= today_start,
            Sale.sale_date < tomorrow_start,
        ).count()

        # Total customers and products
        total_customers = Customer.query.count()
        total_products = Product.query.count()

        # # Total Service Stock Profit
        # total_service_profit = (
        #     db.session.query(
        #         func.coalesce(
        #             func.sum(
        #                 ServiceStock.selling_price - ServiceStock.cost_price
        #             ),
        #             0
        #         )
        #     )
        #     .scalar()
        # )

        # Total completed payments received from sales
        paid_amount = (
            db.session.query(
                func.coalesce(func.sum(Payment.amount), 0)
            )
            .filter(
                func.lower(Payment.status) == "completed",
                Payment.sale_id.isnot(None),
            )
            .scalar()
        )

        # Total completed payments received from sales
        paid_amount = (
            db.session.query(
                func.coalesce(func.sum(Payment.amount), 0)
            )
            .filter(
                func.lower(Payment.status) == "completed",
                Payment.sale_id.isnot(None),
            )
            .scalar()
        )


        # Get all available individual handset counts
        imei_stock_counts = dict(
            db.session.query(
                IMEIRecord.product_id,
                func.count(IMEIRecord.id)
            )
            .filter(
                IMEIRecord.is_active.is_(True),
                IMEIRecord.status == "in_stock",
                IMEIRecord.sale_id.is_(None),
            )
            .group_by(IMEIRecord.product_id)
            .all()
        )

        # Get supplier-purchased handset counts only
        supplier_imei_stock_counts = dict(
            db.session.query(
                IMEIRecord.product_id,
                func.count(IMEIRecord.id)
            )
            .filter(
                IMEIRecord.is_active.is_(True),
                IMEIRecord.status == "in_stock",
                IMEIRecord.sale_id.is_(None),
                db.or_(
                    IMEIRecord.source == "supplier",
                    IMEIRecord.supplier_id.isnot(None),
                ),
            )
            .group_by(IMEIRecord.product_id)
            .all()
        )

        # Calculate total available stock and low stock
        active_products = Product.query.filter(
            Product.is_active.is_(True)
        ).all()

        total_available_stock = 0
        low_stock = 0
        out_of_stock = 0

        for product in active_products:
            if product.track_imei:
                # Total stock includes supplier and direct-seller handsets
                available_quantity = imei_stock_counts.get(product.id, 0)

                # Reorder calculation includes supplier handsets only
                reorder_quantity = supplier_imei_stock_counts.get(product.id, 0)
            else:
                available_quantity = max(int(product.stock_quantity or 0), 0)
                reorder_quantity = available_quantity

            total_available_stock += available_quantity
            reorder_level = max(int(product.reorder_level or 0), 0)

            if available_quantity == 0:
                out_of_stock += 1
            elif 0 < reorder_quantity <= reorder_level:
                low_stock += 1

        # Sales overview for the last seven days
        weekly_sales = []
        weekly_labels = []

        for days_ago in range(6, -1, -1):
            day = today - timedelta(days=days_ago)
            day_start = datetime.combine(day, datetime.min.time())
            next_day_start = day_start + timedelta(days=1)

            day_total = (
                db.session.query(
                    func.coalesce(func.sum(Sale.total_amount), 0)
                )
                .filter(
                    Sale.sale_date >= day_start,
                    Sale.sale_date < next_day_start,
                )
                .scalar()
            )

            weekly_sales.append(float(day_total or 0))
            weekly_labels.append(day.strftime("%a"))

        # Latest eight transactions: include ALL payment statuses
        recent_sales = (
            Sale.query
            .order_by(Sale.sale_date.desc(), Sale.id.desc())
            .limit(8)
            .all()
        )

        # Calculate payment totals for each recent invoice
        recent_transactions = []

        for sale in recent_sales:
            sale_paid = (
                db.session.query(
                    func.coalesce(func.sum(Payment.amount), 0)
                )
                .filter(
                    Payment.sale_id == sale.id,
                    func.lower(Payment.status) == "completed",
                )
                .scalar()
            )

            total_amount = round(float(sale.total_amount or 0), 2)
            paid = round(float(sale_paid or 0), 2)
            balance = round(max(total_amount - paid, 0), 2)

            if balance <= 0:
                payment_status = "Paid"
            elif paid > 0:
                payment_status = "Partially Paid"
            else:
                payment_status = "Unpaid"

            # Most recent completed payment method
            last_payment = (
                Payment.query
                .filter(
                    Payment.sale_id == sale.id,
                    func.lower(Payment.status) == "completed",
                )
                .order_by(Payment.id.desc())
                .first()
            )

            recent_transactions.append({
                "sale": sale,
                "total": total_amount,
                "paid": paid,
                "balance": balance,
                "status": payment_status,
                "method": (
                    last_payment.method.title()
                    if last_payment and last_payment.method
                    else "—"
                ),
            })

        return render_template(
            "dashboard.html",
            user=current_user,
            today_sales=float(today_sales or 0),
            today_orders=today_orders,
            total_customers=total_customers,
            total_products=total_products,
            paid_amount=float(paid_amount or 0),
            low_stock=low_stock,
            weekly_sales=weekly_sales,
            weekly_labels=weekly_labels,
            recent_sales=recent_sales,
            recent_transactions=recent_transactions,
            total_available_stock=total_available_stock,
            out_of_stock=out_of_stock,
        )

    # --------------------------------------------------
    # LOGOUT
    # --------------------------------------------------

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout_page():
        logout_user()
        flash("You have logged out successfully.", "success")
        return redirect(url_for("login_page"))

    # --------------------------------------------------
    # ERROR HANDLERS
    # --------------------------------------------------

    @app.errorhandler(404)
    def not_found(error):
        return "Page not found.", 404

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        app.logger.exception("Internal server error")
        return "An internal server error occurred.", 500

        # --------------------------------------------------

    # SUPPLIER MANAGEMENT
    # --------------------------------------------------

    @app.route("/suppliers")
    @login_required
    def suppliers_list():
        search = request.args.get("search", "").strip()
        status = request.args.get("status", "all").strip().lower()

        query = Supplier.query

        if search:
            term = f"%{search}%"
            query = query.filter(
                or_(
                    Supplier.name.ilike(term),
                    Supplier.company_name.ilike(term),
                    Supplier.contact_person.ilike(term),
                    Supplier.phone.ilike(term),
                    Supplier.mobile_number_2.ilike(term),
                    Supplier.email.ilike(term),
                    Supplier.gst_number.ilike(term),
                )
            )

        if status == "active":
            query = query.filter(Supplier.is_active.is_(True))
        elif status == "inactive":
            query = query.filter(Supplier.is_active.is_(False))

        suppliers = query.order_by(Supplier.name.asc()).all()

        existing_names = [
                row[0]
                for row in db.session.query(Supplier.name)
                .filter(Supplier.name.isnot(None), Supplier.name != "")
                .distinct()
                .order_by(Supplier.name.asc())
                .all()
        ]

        return render_template(
            "suppliers/list.html",
            suppliers=suppliers,
            existing_names=existing_names,
            search=search,
            status=status,
            total_suppliers=Supplier.query.count(),
            active_suppliers=Supplier.query.filter_by(is_active=True).count(),
            inactive_suppliers=Supplier.query.filter_by(is_active=False).count(),
        )

    # --------------------------------------------------
    # SUPPLIER FORM HELPER
    # --------------------------------------------------

    def supplier_form_values():
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        mobile_number_2 = request.form.get("mobile_number_2", "").strip()
        email = request.form.get("email", "").strip()
        lead_time_text = request.form.get("delivery_lead_time", "0").strip()

        if not name:
            raise ValueError("Supplier name is required.")

        if len(name) > 150:
            raise ValueError("Supplier name cannot exceed 150 characters.")

        for label, number in (
            ("Mobile number 1", phone),
            ("Mobile number 2", mobile_number_2),
        ):
            if number and (not number.isdigit() or len(number) != 10):
                raise ValueError(f"{label} must contain exactly 10 digits.")

        if phone and mobile_number_2 and phone == mobile_number_2:
            raise ValueError("The two mobile numbers must be different.")

        if email and (
            "@" not in email
            or email.startswith("@")
            or email.endswith("@")
            or " " in email
        ):
            raise ValueError("Please enter a valid email address.")

        try:
            delivery_lead_time = int(lead_time_text or 0)
        except (ValueError, TypeError):
            raise ValueError("Delivery lead time must be a whole number.")

        if delivery_lead_time < 0:
            raise ValueError("Delivery lead time cannot be negative.")

        return {
            "name": name,
            "company_name": request.form.get("company_name", "").strip() or None,
            "contact_person": request.form.get("contact_person", "").strip() or None,
            "phone": phone or None,
            "mobile_number_2": mobile_number_2 or None,
            "email": email or None,
            "address": request.form.get("address", "").strip() or None,
            "gst_number": request.form.get("gst_number", "").strip().upper() or None,
            "supplier_category": request.form.get("supplier_category", "").strip()
            or None,
            "payment_terms": request.form.get("payment_terms", "").strip() or None,
            "delivery_lead_time": delivery_lead_time,
            "notes": request.form.get("notes", "").strip() or None,
        }

    # --------------------------------------------------
    # CREATE SUPPLIER
    # --------------------------------------------------

    @app.route("/suppliers/create", methods=["GET", "POST"])
    @login_required
    def supplier_create():
        if request.method == "POST":
            try:
                values = supplier_form_values()

                supplier = Supplier(
                    **values,
                    is_active=True,
                )

                db.session.add(supplier)
                db.session.commit()

                flash("Supplier added successfully.", "success")
                return redirect(url_for("suppliers_list"))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                current_app.logger.exception("Supplier creation failed")
                flash("Unable to save the supplier.", "danger")

        return render_template("suppliers/form.html", supplier=None)

    # --------------------------------------------------
    # EDIT SUPPLIER
    # --------------------------------------------------

    @app.route("/suppliers/<int:supplier_id>/edit", methods=["GET", "POST"])
    @login_required
    def supplier_edit(supplier_id):
        supplier = db.session.get(Supplier, supplier_id)

        if supplier is None:
            flash("Supplier not found.", "warning")
            return redirect(url_for("suppliers_list"))

        if request.method == "POST":
            try:
                values = supplier_form_values()

                for field, value in values.items():
                    setattr(supplier, field, value)

                db.session.commit()

                flash("Supplier updated successfully.", "success")
                return redirect(url_for("supplier_detail", supplier_id=supplier.id))

            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")

            except Exception:
                db.session.rollback()
                current_app.logger.exception("Supplier update failed")
                flash("Unable to update the supplier.", "danger")

        return render_template("suppliers/form.html", supplier=supplier)

    # --------------------------------------------------
    # SUPPLIER DETAILS
    # --------------------------------------------------

    @app.route("/suppliers/<int:supplier_id>")
    @login_required
    def supplier_detail(supplier_id):
        supplier = db.session.get(Supplier, supplier_id)

        if supplier is None:
            flash("Supplier not found.", "warning")
            return redirect(url_for("suppliers_list"))

        # Regular purchase records
        purchases = (
            Purchase.query
            .filter_by(supplier_id=supplier.id)
            .order_by(Purchase.purchase_date.desc())
            .all()
        )

        # Direct stock records linked to this supplier
                # Date filter values
        from_date = request.args.get("from_date", "").strip()
        to_date = request.args.get("to_date", "").strip()

        # Validate date range
        try:
            from_date_obj = date.fromisoformat(from_date) if from_date else None
            to_date_obj = date.fromisoformat(to_date) if to_date else None

            if from_date_obj and to_date_obj and from_date_obj > to_date_obj:
                raise ValueError

        except ValueError:
            flash("Please select a valid date range.", "warning")
            return redirect(url_for("supplier_detail", supplier_id=supplier.id))

        # Supplier Add Stock IMEI records
        direct_stock_query = IMEIRecord.query.filter_by(
            supplier_id=supplier.id,
            source="supplier",
            purchase_item_id=None
        )


        # All supplier IMEI records for historical contribution
        supplier_contribution_query = IMEIRecord.query.filter_by(
            supplier_id=supplier.id,
            source="supplier"
        )

        # Apply the existing date filters
        if from_date_obj:
            supplier_contribution_query = supplier_contribution_query.filter(
                IMEIRecord.purchase_date >= from_date_obj
            )

        if to_date_obj:
            supplier_contribution_query = supplier_contribution_query.filter(
                IMEIRecord.purchase_date <= to_date_obj
            )

        supplier_contribution_records = (
            supplier_contribution_query
            .order_by(
                IMEIRecord.purchase_date.desc(),
                IMEIRecord.id.desc()
            )
            .all()
        )


        # Calculate historical supplier contribution totals
        supplier_contribution_total = len(supplier_contribution_records)

        supplier_used_quantity = sum(
            1 for record in supplier_contribution_records
            if (record.condition or "").strip().lower() == "used"
        )

        supplier_sealed_quantity = sum(
            1 for record in supplier_contribution_records
            if (record.condition or "").strip().lower() == "sealed"
        )

        supplier_contribution_value = sum(
            float(record.purchase_price or 0)
            for record in supplier_contribution_records
        )

        # Group contribution by product, variant, colour and condition
        supplier_contribution_summary = {}

        for record in supplier_contribution_records:
            key = (
                record.product_id,
                record.variant or "",
                record.color or "",
                record.condition or "",
                record.used_grade or "",
            )

            if key not in supplier_contribution_summary:
                supplier_contribution_summary[key] = {
                    "product": record.product,
                    "variant": record.variant,
                    "color": record.color,
                    "condition": record.condition,
                    "used_grade": record.used_grade,
                    "quantity": 0,
                    "purchase_total": 0,
                }

            supplier_contribution_summary[key]["quantity"] += 1
            supplier_contribution_summary[key]["purchase_total"] += float(
                record.purchase_price or 0
            )

        supplier_contribution_summary = list(
            supplier_contribution_summary.values()
        )



        # Non-IMEI Supplier Add Stock movements
        movements_query = StockMovement.query.filter(
            StockMovement.movement_type == "supplier_stock",
            StockMovement.notes == f"Stock added from supplier: {supplier.name}"
        )

        # Apply From Date to both
        if from_date_obj:
            direct_stock_query = direct_stock_query.filter(
                IMEIRecord.purchase_date >= from_date_obj
            )

            movements_query = movements_query.filter(
                StockMovement.created_at >= datetime.combine(
                    from_date_obj, datetime.min.time()
                )
            )

        # Apply To Date to both
        if to_date_obj:
            direct_stock_query = direct_stock_query.filter(
                IMEIRecord.purchase_date <= to_date_obj
            )

            movements_query = movements_query.filter(
                StockMovement.created_at < datetime.combine(
                    to_date_obj + timedelta(days=1),
                    datetime.min.time()
                )
            )

        direct_stock_records = (
            direct_stock_query
            .order_by(
                IMEIRecord.purchase_date.desc(),
                IMEIRecord.id.desc()
            )
            .all()
        )

        supplier_movements = (
            movements_query
            .order_by(StockMovement.created_at.desc())
            .all()
        )

        # Group direct stock records by handset/product
        direct_stock_summary = {}

        for record in direct_stock_records:
            key = (
                record.product_id,
                record.variant or "",
                record.color or "",
                record.condition or "",
                record.used_grade or "",
            )

            if key not in direct_stock_summary:
                direct_stock_summary[key] = {
                    "product": record.product,
                    "variant": record.variant,
                    "color": record.color,
                    "condition": record.condition,
                    "used_grade": record.used_grade,
                    "quantity": 0,
                    "purchase_total": 0,
                }

            direct_stock_summary[key]["quantity"] += 1
            direct_stock_summary[key]["purchase_total"] += float(
                record.purchase_price or 0
            )

        direct_stock_summary = list(direct_stock_summary.values())


        # Calculate totals
        regular_purchase_total = sum(
            float(purchase.total_amount or 0)
            for purchase in purchases
        )

        regular_purchase_paid = sum(
            float(payment.amount or 0)
            for purchase in purchases
            for payment in purchase.payments
            if payment.status == "completed"
        )

        regular_purchase_due = max(
            regular_purchase_total - regular_purchase_paid,
            0
        )

        direct_stock_total = sum(
            float(record.purchase_price or 0)
            for record in direct_stock_records
        )

                # Direct Stock account records for this supplier
        direct_account_query = IMEIRecord.query.filter_by(
            supplier_id=supplier.id,
            source="supplier",
            purchase_item_id=None
        )

        # Calculate the complete account total (ignore date filters)
        direct_account_all_records = direct_account_query.all()

        direct_account_stock_total = sum(
            float(record.purchase_price or 0)
            for record in direct_account_all_records
        )

        # Display records using the selected date range
        if from_date_obj:
            direct_account_query = direct_account_query.filter(
                IMEIRecord.purchase_date >= from_date_obj
            )

        if to_date_obj:
            direct_account_query = direct_account_query.filter(
                IMEIRecord.purchase_date <= to_date_obj
            )

        direct_account_stock_records = (
            direct_account_query
            .order_by(
                IMEIRecord.purchase_date.desc(),
                IMEIRecord.id.desc()
            )
            .all()
        )

        # All payments made against this supplier's Direct Stock account
        direct_account_all_payments = (
            DirectStockPayment.query
            .filter_by(supplier_id=supplier.id)
            .all()
        )

        direct_account_paid = sum(
            float(payment.amount or 0)
            for payment in direct_account_all_payments
        )

        # Remaining Direct Stock outstanding
        direct_account_due = max(
            direct_account_stock_total - direct_account_paid,
            0
        )

        # Payment history (respect the selected date range)
        direct_account_payments_query = DirectStockPayment.query.filter_by(
            supplier_id=supplier.id
        )

        if from_date_obj:
            direct_account_payments_query = direct_account_payments_query.filter(
                DirectStockPayment.payment_date >= from_date_obj
            )

        if to_date_obj:
            direct_account_payments_query = direct_account_payments_query.filter(
                DirectStockPayment.payment_date <= to_date_obj
            )

        direct_account_payments = (
            direct_account_payments_query
            .order_by(
                DirectStockPayment.payment_date.desc(),
                DirectStockPayment.id.desc()
            )
            .all()
        )

        purchase_total = direct_stock_total

        return render_template(
            "suppliers/detail.html",
            supplier=supplier,
            purchases=purchases,
            direct_stock_records=direct_stock_records,
            supplier_movements=supplier_movements,
            purchase_total=purchase_total,
            regular_purchase_total=regular_purchase_total,
            direct_stock_total=direct_stock_total,
            regular_purchase_paid=regular_purchase_paid,
            regular_purchase_due=regular_purchase_due,
            direct_stock_summary=direct_stock_summary,
            from_date=from_date,
            to_date=to_date,
            direct_account_stock_records=direct_account_stock_records,
            direct_account_stock_total=direct_account_stock_total,
            direct_account_paid=direct_account_paid,
            direct_account_due=direct_account_due,
            direct_account_payments=direct_account_payments,
            supplier_contribution_records=supplier_contribution_records,
            supplier_contribution_total=supplier_contribution_total,
            supplier_used_quantity=supplier_used_quantity,
            supplier_sealed_quantity=supplier_sealed_quantity,
            supplier_contribution_value=supplier_contribution_value,
            supplier_contribution_summary=supplier_contribution_summary,

        )

    @app.route(
        "/suppliers/<int:supplier_id>/direct-stock-payment",
        methods=["POST"]
    )
    @login_required
    def supplier_direct_stock_payment(supplier_id):
        from decimal import Decimal, InvalidOperation

        supplier = db.session.get(Supplier, supplier_id)

        if supplier is None:
            flash("Supplier not found.", "warning")
            return redirect(url_for("suppliers_list"))

        # Validate payment amount
        try:
            amount = Decimal(request.form.get("amount", "").strip())

            if not amount.is_finite() or amount <= 0:
                raise ValueError

            amount = amount.quantize(Decimal("0.01"))

            payment_date = date.fromisoformat(
                request.form.get("payment_date", "").strip()
            )

        except (InvalidOperation, ValueError, AttributeError):
            flash("Please enter a valid payment amount and date.", "warning")
            return redirect(
                url_for("supplier_detail", supplier_id=supplier.id)
            )

        # Validate payment method
        payment_method = request.form.get(
            "payment_method", "cash"
        ).strip().lower()

        allowed_methods = {
            "cash",
            "upi",
            "bank_transfer",
            "card",
            "other"
        }

        if payment_method not in allowed_methods:
            flash("Invalid payment method selected.", "warning")
            return redirect(
                url_for("supplier_detail", supplier_id=supplier.id)
            )

        reference = request.form.get("reference", "").strip() or None
        notes = request.form.get("notes", "").strip() or None

        if reference and len(reference) > 100:
            flash("Reference number cannot exceed 100 characters.", "warning")
            return redirect(
                url_for("supplier_detail", supplier_id=supplier.id)
            )

        # Recalculate complete Direct Stock value for this supplier
        stock_total = db.session.query(
            db.func.coalesce(
                db.func.sum(IMEIRecord.purchase_price), 0
            )
        ).filter_by(
            supplier_id=supplier.id,
            source="supplier",
            purchase_item_id=None
        ).scalar()

        # Recalculate previous Direct Stock payments
        paid_total = db.session.query(
            db.func.coalesce(
                db.func.sum(DirectStockPayment.amount), 0
            )
        ).filter_by(
            supplier_id=supplier.id
        ).scalar()

        outstanding = (
            Decimal(str(stock_total or 0))
            - Decimal(str(paid_total or 0))
        ).quantize(Decimal("0.01"))

        # Prevent overpayment
        if outstanding <= 0:
            flash("This supplier has no Direct Stock outstanding.", "info")
            return redirect(
                url_for("supplier_detail", supplier_id=supplier.id)
            )

        if amount > outstanding:
            flash(
                f"Payment exceeds outstanding balance of ₹{outstanding:,.2f}.",
                "warning"
            )
            return redirect(
                url_for("supplier_detail", supplier_id=supplier.id)
            )

        # Save payment
        try:
            payment = DirectStockPayment(
                supplier_id=supplier.id,
                amount=float(amount),
                payment_date=payment_date,
                payment_method=payment_method,
                reference=reference,
                notes=notes
            )

            db.session.add(payment)
            db.session.commit()

            flash(
                f"Direct Stock payment of ₹{amount:,.2f} recorded successfully.",
                "success"
            )

        except Exception:
            db.session.rollback()
            flash("Could not save Direct Stock payment. Please try again.", "danger")

        return redirect(
            url_for("supplier_detail", supplier_id=supplier.id)
        )


    # --------------------------------------------------
    # ACTIVATE / DEACTIVATE SUPPLIER
    # --------------------------------------------------

    @app.route(
        "/suppliers/<int:supplier_id>/toggle-status",
        methods=["POST"],
    )
    @login_required
    def supplier_toggle_status(supplier_id):
        supplier = db.session.get(Supplier, supplier_id)

        if supplier is None:
            flash("Supplier not found.", "warning")
            return redirect(url_for("suppliers_list"))

        supplier.is_active = not supplier.is_active

        try:
            db.session.commit()

            message = "activated" if supplier.is_active else "deactivated"
            flash(f"Supplier {message} successfully.", "success")

        except Exception:
            db.session.rollback()
            current_app.logger.exception("Supplier status update failed")
            flash("Unable to change supplier status.", "danger")

        return redirect(url_for("suppliers_list"))

    # --------------------------------------------------
    # PURCHASES: LIST
    # --------------------------------------------------
    
    @app.route("/purchases")
    @login_required
    def purchase_list():
        search = request.args.get("search", "").strip()
        supplier_id = request.args.get("supplier_id", type=int)
        status = request.args.get("status", "").strip()
        payment_status = request.args.get("payment_status", "").strip()
        selected_company = request.args.get("company_name", "").strip()

        source_filter = request.args.get("source", "all").strip().lower()

        if source_filter not in ("all", "supplier", "seller"):
            source_filter = "all"

        query = Purchase.query.join(Supplier)

        if search:
            term = f"%{search}%"
            query = query.filter(
                db.or_(
                    Purchase.invoice_number.ilike(term),
                    Supplier.name.ilike(term),
                    Supplier.company_name.ilike(term),
                )
            )

        if supplier_id:
            query = query.filter(
                Purchase.supplier_id == supplier_id
            )

        if selected_company:
            query = query.filter(
                Supplier.company_name == selected_company
            )

        if status:
            query = query.filter(
                Purchase.status == status
            )

        seller_records_query = (
            IMEIRecord.query
            .join(Product, IMEIRecord.product_id == Product.id)
            .join(
                UsedHandsetSeller,
                UsedHandsetSeller.imei_record_id == IMEIRecord.id
            )
        )

        if search:
            term = f"%{search}%"
            seller_records_query = seller_records_query.filter(
                db.or_(
                    UsedHandsetSeller.seller_name.ilike(term),
                    UsedHandsetSeller.seller_phone.ilike(term),
                    Product.name.ilike(term),
                    Product.model.ilike(term),
                    IMEIRecord.imei1.ilike(term),
                    IMEIRecord.imei2.ilike(term),
                )
            )

        seller_records = (
            seller_records_query
            .order_by(IMEIRecord.purchase_date.desc(), IMEIRecord.id.desc())
            .all()
        )

        purchases = query.order_by(Purchase.id.desc()).all()

        if payment_status:
            purchases = [
                purchase for purchase in purchases
                if purchase.payment_status.lower() == payment_status.lower()
            ]

        if source_filter == "seller":
            purchases = []

        elif source_filter == "supplier":
            seller_records = []

        suppliers = (
            Supplier.query
            .filter_by(is_active=True)
            .order_by(Supplier.company_name, Supplier.name)
            .all()
        )

        companies = sorted({
            supplier.company_name.strip()
            for supplier in suppliers
            if supplier.company_name and supplier.company_name.strip()
        }, key=str.lower)

        return render_template(
            "purchases/list.html",
            purchases=purchases,
            suppliers=suppliers,
            companies=companies,
            search=search,
            supplier_id=supplier_id,
            status=status,
            payment_status=payment_status,
            selected_company=selected_company,
            seller_records=seller_records,
            source_filter=source_filter,
        )

    # --------------------------------------------------
    # PURCHASES: CREATE
    # --------------------------------------------------

    @app.route("/purchases/create", methods=["GET", "POST"])
    @login_required
    def purchase_create():
        suppliers = (
            Supplier.query.filter_by(is_active=True).order_by(Supplier.name).all()
        )

        products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

        individual_handsets = (
            IMEIRecord.query
            .filter_by(
                source="direct",
                status="in_stock",
            )
            .order_by(IMEIRecord.id.desc())
            .all()
        )

        if request.method == "GET":
            return render_template(
                "purchases/form.html",
                suppliers=suppliers,
                products=products,
                individual_handsets=individual_handsets,
            )

        try:
            last_purchase = Purchase.query.filter(
                Purchase.invoice_number.like("SUP-INV-%")
            ).order_by(Purchase.id.desc()).first()

            next_number = 1001

            if last_purchase and last_purchase.invoice_number:
                try:
                    last_number = int(
                        last_purchase.invoice_number.rsplit("-", 1)[1]
                    )
                    next_number = max(1001, last_number + 1)
                except (ValueError, IndexError):
                    pass

            invoice_number = f"SUP-INV-{next_number:04d}"

            supplier_id = request.form.get("supplier_id", type=int)

            supplier = (
                db.session.get(Supplier, supplier_id)
                if supplier_id else None
            )

            # if not invoice_number:
            #     raise ValueError("Enter the supplier invoice number.")

            if not supplier or not supplier.is_active:
                raise ValueError("Select a valid active supplier.")

            existing_purchase = Purchase.query.filter_by(
                invoice_number=invoice_number
            ).first()

            if existing_purchase:
                raise ValueError("This invoice number already exists.")

            purchase_date_raw = request.form.get("purchase_date", "").strip()

            purchase_date = (
                datetime.strptime(purchase_date_raw, "%Y-%m-%d")
                if purchase_date_raw
                else datetime.now()
            )

            def parse_optional_date(field_name):
                raw_date = request.form.get(field_name, "").strip()

                if not raw_date:
                    return None

                return datetime.strptime(raw_date, "%Y-%m-%d").date()

            expected_delivery_date = parse_optional_date("expected_delivery_date")

            payment_due_date = parse_optional_date("payment_due_date")

            product_ids = request.form.getlist("product_id[]")
            quantities = request.form.getlist("quantity[]")
            unit_costs = request.form.getlist("unit_cost[]")

            if not product_ids:
                raise ValueError("Add at least one purchase item.")

            if not (len(product_ids) == len(quantities) == len(unit_costs)):
                raise ValueError("Purchase item details are incomplete.")

            purchase = Purchase(
                invoice_number=invoice_number,
                supplier_id=supplier.id,
                user_id=current_user.id,
                purchase_date=purchase_date,
                expected_delivery_date=expected_delivery_date,
                payment_due_date=payment_due_date,
                total_amount=0,
                status="ordered",
            )

            db.session.add(purchase)
            db.session.flush()

            total_amount = 0.0
            item_count = 0

            for raw_id, raw_quantity, raw_cost in zip(
                product_ids,
                quantities,
                unit_costs,
            ):
                if not raw_id and not raw_quantity and not raw_cost:
                    continue

                try:
                    product_id = int(raw_id)
                    quantity = int(raw_quantity)
                    unit_cost = float(raw_cost)
                except (TypeError, ValueError):
                    raise ValueError("Enter valid product, quantity, and unit cost.")

                product = db.session.get(Product, product_id)

                if not product or not product.is_active:
                    raise ValueError("A selected product is unavailable.")

                if quantity <= 0:
                    raise ValueError("Quantity must be greater than zero.")

                if unit_cost < 0:
                    raise ValueError("Unit cost cannot be negative.")

                subtotal = round(quantity * unit_cost, 2)
                total_amount += subtotal
                item_count += 1

                purchase_item = PurchaseItem(
                    purchase_id=purchase.id,
                    product_id=product.id,
                    quantity=quantity,
                    unit_cost=unit_cost,
                    subtotal=subtotal,
                )

                db.session.add(purchase_item)

            if item_count == 0:
                raise ValueError("Add at least one valid purchase item.")

            purchase.total_amount = round(total_amount, 2)

            db.session.commit()

            flash(
                "Purchase order created successfully.",
                "success",
            )

            return redirect(
                url_for(
                    "purchase_detail",
                    purchase_id=purchase.id,
                )
            )

        except ValueError as error:
            db.session.rollback()
            flash(str(error), "danger")

        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to create purchase order.")
            flash(
                "Could not create the purchase order. " "Check the application log.",
                "danger",
            )

        return render_template(
            "purchases/form.html",
            suppliers=suppliers,
            products=products,
            individual_handsets=individual_handsets,
        )
        
    # PURCHASES: DETAIL

    @app.route("/purchases/<int:purchase_id>")
    @login_required
    def purchase_detail(purchase_id):
        purchase = db.session.get(Purchase, purchase_id)

        if not purchase:
            flash("Purchase order not found.", "danger")
            return redirect(url_for("purchase_list"))

        return render_template(
            "purchases/detail.html",
            purchase=purchase,
        )
        
    
    @app.route("/purchases/<int:purchase_id>/edit", methods=["GET", "POST"])
    @login_required
    def purchase_edit(purchase_id):
        purchase = db.session.get(Purchase, purchase_id)

        if not purchase:
            flash("Purchase order not found.", "danger")
            return redirect(url_for("purchase_list"))

        suppliers = (
            Supplier.query
            .filter_by(is_active=True)
            .order_by(Supplier.name)
            .all()
        )

        if request.method == "POST":
            try:
                supplier_id = request.form.get("supplier_id", type=int)
                supplier = (
                    db.session.get(Supplier, supplier_id)
                    if supplier_id else None
                )

                if not supplier or not supplier.is_active:
                    raise ValueError("Select a valid active supplier.")

                purchase_date_raw = request.form.get(
                    "purchase_date", ""
                ).strip()

                if not purchase_date_raw:
                    raise ValueError("Enter the purchase date.")

                purchase.supplier_id = supplier.id
                purchase.purchase_date = datetime.strptime(
                    purchase_date_raw, "%Y-%m-%d"
                )

                def parse_edit_date(field_name):
                    raw = request.form.get(field_name, "").strip()
                    return (
                        datetime.strptime(raw, "%Y-%m-%d").date()
                        if raw else None
                    )

                purchase.expected_delivery_date = parse_edit_date(
                    "expected_delivery_date"
                )
                purchase.actual_delivery_date = parse_edit_date(
                    "actual_delivery_date"
                )
                purchase.payment_due_date = parse_edit_date(
                    "payment_due_date"
                )

                db.session.commit()
                flash("Purchase updated successfully.", "success")

                return redirect(
                    url_for(
                        "purchase_detail",
                        purchase_id=purchase.id,
                    )
                )

            except ValueError as error:
                db.session.rollback()
                flash(str(error), "danger")

            except Exception:
                db.session.rollback()
                app.logger.exception("Failed to update purchase.")
                flash("Could not update purchase.", "danger")

        return render_template(
            "purchases/edit.html",
            purchase=purchase,
            suppliers=suppliers,
        )
    
        # PURCHASES: RECEIVE

    @app.route(
        "/purchases/<int:purchase_id>/receive",
        methods=["POST"],
    )
    @login_required
    def purchase_receive(purchase_id):

        purchase = db.session.get(Purchase, purchase_id)

        if not purchase:
            flash("Purchase order not found.", "danger")
            return redirect(url_for("purchase_list"))

        if purchase.status != "ordered":
            flash(
                "Only ordered purchases can be received.",
                "warning"
            )
            return redirect(
                url_for("purchase_detail", purchase_id=purchase.id)
            )

        try:
            items = PurchaseItem.query.filter_by(
                purchase_id=purchase.id
            ).all()

            if not items:
                raise ValueError("This purchase has no items.")

            # Store IMEI values to check duplicates within this receipt
            received_imeis = set()

            for item in items:

                product = db.session.get(Product, item.product_id)

                if not product:
                    raise ValueError(
                        f"Product for purchase item {item.id} was not found."
                    )

                # Validate and register individual handsets
                if product.track_imei:

                    for unit in range(item.quantity):

                        imei1 = request.form.get(
                            f"imei1_{item.id}_{unit}", ""
                        ).strip()

                        imei2 = request.form.get(
                            f"imei2_{item.id}_{unit}", ""
                        ).strip()

                        # IMEI1 is mandatory
                        if not imei1:
                            raise ValueError(
                                f"IMEI1 is required for {product.name}, "
                                f"handset {unit + 1}."
                            )

                        # Validate IMEI1 format
                        if not imei1.isdigit() or len(imei1) != 15:
                            raise ValueError(
                                f"IMEI1 must contain exactly 15 digits "
                                f"for {product.name}, handset {unit + 1}."
                            )

                        # IMEI2 is optional
                        if imei2 and (
                            not imei2.isdigit() or len(imei2) != 15
                        ):
                            raise ValueError(
                                f"IMEI2 must contain exactly 15 digits "
                                f"for {product.name}, handset {unit + 1}."
                            )

                        # Check IMEI1 and IMEI2 are not identical
                        if imei2 and imei1 == imei2:
                            raise ValueError(
                                "IMEI1 and IMEI2 cannot be identical."
                            )

                        # Check duplicate IMEIs within this purchase
                        handset_imeis = [imei1]

                        if imei2:
                            handset_imeis.append(imei2)

                        for imei in handset_imeis:

                            if imei in received_imeis:
                                raise ValueError(
                                    f"Duplicate IMEI entered: {imei}"
                                )

                            received_imeis.add(imei)

                        # Check duplicate IMEIs in existing database
                        existing_imei = IMEIRecord.query.filter(
                            db.or_(
                                IMEIRecord.imei1.in_(handset_imeis),
                                IMEIRecord.imei2.in_(handset_imeis)
                            )
                        ).first()

                        if existing_imei:
                            raise ValueError(
                                "IMEI already exists in inventory: "
                                f"{existing_imei.imei1}"
                            )


                        # Get individual handset specifications
                        variant = request.form.get(
                            f"variant_{item.id}_{unit}", ""
                        ).strip() or None

                        color = request.form.get(
                            f"color_{item.id}_{unit}", ""
                        ).strip() or None

                        # Get and validate individual handset purchase cost
                        purchase_price_raw = request.form.get(
                            f"purchase_price_{item.id}_{unit}", ""
                        ).strip()

                        if not purchase_price_raw:
                            raise ValueError(
                                f"Purchase Cost is required for {product.name}, "
                                f"handset {unit + 1}."
                            )

                        try:
                            purchase_price = float(purchase_price_raw)
                        except ValueError:
                            raise ValueError(
                                f"Enter a valid Purchase Cost for {product.name}, "
                                f"handset {unit + 1}."
                            )

                        if not 0 <= purchase_price < float("inf"):
                            raise ValueError(
                                f"Purchase Cost cannot be negative or invalid for "
                                f"{product.name}, handset {unit + 1}."
                            )

                        # Get and validate individual handset selling price
                        selling_price_raw = request.form.get(
                            f"selling_price_{item.id}_{unit}", ""
                        ).strip()

                        if not selling_price_raw:
                            raise ValueError(
                                f"Selling Price is required for {product.name}, "
                                f"handset {unit + 1}."
                            )

                        try:
                            selling_price = float(selling_price_raw)
                        except ValueError:
                            raise ValueError(
                                f"Enter a valid Selling Price for {product.name}, "
                                f"handset {unit + 1}."
                            )

                        if not 0 <= selling_price < float("inf"):
                            raise ValueError(
                                f"Selling Price cannot be negative or invalid for "
                                f"{product.name}, handset {unit + 1}."
                            )

                        # Get and validate individual handset GST
                        gst_raw = request.form.get(
                            f"gst_percent_{item.id}_{unit}", ""
                        ).strip()

                        if not gst_raw:
                            raise ValueError(
                                f"GST is required for {product.name}, handset {unit + 1}."
                        )

                        try:
                            gst_percent = float(gst_raw or 0)
                        except ValueError:
                            raise ValueError(
                                f"Enter a valid GST percentage for "
                                f"{product.name}, handset {unit + 1}."
                            )

                        if not 0 <= gst_percent <= 100:
                            raise ValueError(
                                f"GST must be between 0 and 100% for "
                                f"{product.name}, handset {unit + 1}."
                            )

                        # Get and validate individual warranty duration
                        warranty_raw = request.form.get(
                            f"warranty_months_{item.id}_{unit}",
                            str(product.warranty_months or 0)
                        ).strip()

                        try:
                            warranty_months = int(warranty_raw or 0)
                        except ValueError:
                            raise ValueError(
                                f"Enter a valid warranty duration for "
                                f"{product.name}, handset {unit + 1}."
                            )

                        if warranty_months < 0 or warranty_months > 120:
                            raise ValueError(
                                f"Warranty must be between 0 and 120 months "
                                f"for {product.name}, handset {unit + 1}."
                            )

                        handset = IMEIRecord(
                            product_id=product.id,
                            purchase_item_id=item.id,
                            supplier_id=purchase.supplier_id,
                            source="supplier",

                            imei1=imei1,
                            imei2=imei2 or None,

                            purchase_date=(
                                purchase.purchase_date.date()
                                if purchase.purchase_date
                                else datetime.now().date()
                            ),

                            purchase_price=purchase_price,
                            selling_price=selling_price,

                            # Individual handset specifications
                            variant=variant,
                            color=color,
                            gst_percent=gst_percent,
                            condition=product.condition,
                            used_grade=product.used_grade,

                            # Individual warranty
                            warranty_months=warranty_months,


                            status="in_stock",
                        )

                        db.session.add(handset)


                # Update product stock
                product.stock_quantity += item.quantity

                # Update product cost price
                product.cost_price = item.unit_cost

                # Create stock movement
                movement = StockMovement(
                    product_id=product.id,
                    user_id=current_user.id,
                    movement_type="purchase",
                    quantity_change=item.quantity,
                    reference=purchase.invoice_number,
                    notes="Stock received from supplier",
                )

                db.session.add(movement)

            # Mark purchase as received
            purchase.status = "received"

            purchase.actual_delivery_date = datetime.now().date()

            db.session.commit()

            flash(
                "Purchase received successfully. "
                "Handsets registered and stock updated.",
                "success"
            )

        except ValueError as error:
            db.session.rollback()
            flash(str(error), "danger")

        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to receive purchase.")
            flash("Could not receive this purchase.", "danger")

        return redirect(
            url_for("purchase_detail", purchase_id=purchase.id)
        )
   
    @app.route("/purchases/<int:purchase_id>/payment", methods=["POST"])
    @login_required
    def purchase_add_payment(purchase_id):
        purchase = db.session.get(Purchase, purchase_id)

        if not purchase:
            flash("Purchase order not found.", "danger")
            return redirect(url_for("purchase_list"))

        try:
            amount = float(request.form.get("amount", "0"))
            method = request.form.get("method", "cash").strip()
            reference = request.form.get("reference", "").strip()

            if amount <= 0:
                raise ValueError("Payment must be greater than zero.")

            if amount > purchase.balance_due:
                raise ValueError("Payment cannot exceed the balance due.")

            if purchase.status != "received":
                raise ValueError("Receive the purchase before recording payment.")

            payment = Payment(
                purchase_id=purchase.id,
                amount=amount,
                method=method,
                reference=reference,
                status="completed",
            )

            db.session.add(payment)
            db.session.commit()

            flash("Supplier payment recorded successfully.", "success")

        except ValueError as error:
            db.session.rollback()
            flash(str(error), "danger")

        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to record supplier payment.")
            flash("Could not record the payment.", "danger")

        return redirect(
            url_for("purchase_detail", purchase_id=purchase.id)
        )

    # --------------------------------------------------
    # SERVICE STOCK
    # --------------------------------------------------

    return app

def service_stock():

    if request.method == "POST":
        try:
            purchased_date_raw = request.form.get(
                "purchased_date", ""
            ).strip()

            product_name = request.form.get(
                "product_name", ""
            ).strip()

            stock_name = request.form.get(
                "stock_name", ""
            ).strip()

            supplier_name = request.form.get(
                "supplier_name", ""
            ).strip()

            contact = request.form.get(
                "contact", ""
            ).strip()

            quantity_raw = request.form.get(
                "quantity", "1"
            ).strip()

            cost_price_raw = request.form.get(
                "cost_price", ""
            ).strip()

            selling_price_raw = request.form.get(
                "selling_price", ""
            ).strip()

            # Required field validation
            if not purchased_date_raw:
                raise ValueError("Please select purchased date.")

            if not product_name:
                raise ValueError("Please enter product name.")

            if not stock_name:
                raise ValueError("Please enter stock name.")

            if not cost_price_raw or not selling_price_raw:
                raise ValueError(
                    "Please enter both cost price and selling price."
                )

            # Date conversion
            purchased_date = datetime.strptime(
                purchased_date_raw,
                "%Y-%m-%d"
            ).date()

            # Quantity conversion
            quantity = int(quantity_raw)

            if quantity <= 0:
                raise ValueError("Quantity must be greater than zero.")

            # Price conversion
            cost_price = float(cost_price_raw)
            selling_price = float(selling_price_raw)

            # Price validation
            if not math.isfinite(cost_price) or cost_price < 0:
                raise ValueError("Invalid cost price.")

            if not math.isfinite(selling_price) or selling_price < 0:
                raise ValueError("Invalid selling price.")

            # Create service stock record
            stock = ServiceStock(
                purchased_date=purchased_date,
                product_name=product_name,
                stock_name=stock_name,
                supplier_name=supplier_name or None,
                contact=contact,
                quantity=quantity,
                available_quantity=quantity,
                cost_price=cost_price,
                selling_price=selling_price
            )

            db.session.add(stock)
            db.session.commit()

            flash("Service stock added successfully.", "success")

            return redirect(url_for("service_stock"))

        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")

        except Exception:
            db.session.rollback()
            current_app.logger.exception("Service stock creation failed")
            flash(
                "Unable to add service stock. Please try again.",
                "danger"
            )

    stocks = ServiceStock.query.order_by(
        ServiceStock.purchased_date.desc(),
        ServiceStock.id.desc()
    ).all()

    total_service_profit = sum(
        (stock.service_profit for stock in stocks),
        start=0
    )

    return render_template(
        "services/stock.html",
        stocks=stocks,
        total_service_profit=total_service_profit
    )

    # --------------------------------------------------
    # PRODUCT LIST
    # --------------------------------------------------


@app.route("/inventory/direct-stock", methods=["GET", "POST"])
@login_required
def direct_stock_register():

    # ==================================================
    # GET: LOAD PRODUCTS AND EXISTING HANDSETS
    # ==================================================
    
    if request.method == "GET":

        products = (
            Product.query
            .filter_by(is_active=True)
            .order_by(Product.name.asc())
            .all()
        )

        suppliers = (
            Supplier.query
            .filter_by(is_active=True)
            .order_by(Supplier.name.asc())
            .all()
        )

        selected_product_id = request.args.get(
            "product_id",
            type=int
        )

        selected_product = None
        existing_handset_records = []

        if selected_product_id:

            selected_product = Product.query.filter_by(
                id=selected_product_id,
                is_active=True
            ).first()

            if not selected_product:

                flash(
                    "Selected product was not found.",
                    "danger"
                )

            else:

                existing_handset_records = (
                    IMEIRecord.query
                    .filter_by(
                        product_id=selected_product.id,
                        status="in_stock"
                    )
                    .order_by(IMEIRecord.id.desc())
                    .limit(30)
                    .all()
                )

        # --------------------------------------------------
        # LOAD ALL COMPLETE HANDSET RECORDS FOR FILTERING
        # No activation or stock-status filter is applied.
        # --------------------------------------------------

        all_handset_records = (
            IMEIRecord.query
            .join(
                Product,
                IMEIRecord.product_id == Product.id
            )
            .order_by(IMEIRecord.id.desc())
            .all()
        )

        handset_filter_data = []

        for record in all_handset_records:

            product = record.product
            supplier = record.supplier
            seller = record.used_handset_seller

            handset_filter_data.append({

                "product_id": record.product_id,

                # Product details
                "product_name": product.name or "",
                "brand": product.brand or "",
                "model": product.model or "",
                "product_code": product.product_code or "",
                "category": product.category or "",

                # Individual handset details
                "imei1": record.imei1 or "",
                "imei2": record.imei2 or "",
                "variant": record.variant or "",
                "color": record.color or "",
                "condition": record.condition or "",
                "used_grade": record.used_grade or "",

                # Pricing and warranty
                "gst_percent": str(record.gst_percent or 0),
                "purchase_price": str(record.purchase_price or 0),
                "selling_price": str(record.selling_price or 0),
                "warranty_months": str(record.warranty_months or 0),

                # Supplier details
                "supplier_name": (
                    (supplier.company_name or supplier.name or "")
                    if supplier else ""
                ),
                "supplier_contact": (
                    supplier.contact_person or ""
                    if supplier else ""
                ),
                "supplier_phone": (
                    supplier.phone or ""
                    if supplier else ""
                ),
                "supplier_mobile_2": (
                    supplier.mobile_number_2 or ""
                    if supplier else ""
                ),

                # Individual used seller details
                "seller_name": (
                    seller.seller_name or ""
                    if seller else ""
                ),
                "seller_phone": (
                    seller.seller_phone or ""
                    if seller else ""
                ),
                "seller_address": (
                    seller.seller_address or ""
                    if seller else ""
                ),

                # Handset status
                "stock_status": record.status or "",
                "activation_status": (
                    "active" if record.is_active else "inactive"
                ),
                "source": record.source or "",

                # Purchase date and invoice
                "purchase_date": (
                    record.purchase_date.strftime("%d-%m-%Y")
                    if record.purchase_date else ""
                ),
                "invoice_number": (
                    record.sale.invoice_number
                    if record.sale and record.sale.invoice_number
                    else ""
                ),

            })

        return render_template(
            "inventory/direct_stock.html",
            products=products,
            selected_product=selected_product,
            selected_product_id=selected_product_id,
            existing_handset_records=existing_handset_records,
            handset_filter_data=handset_filter_data,
            suppliers=suppliers,
        )


    # ==================================================
    # POST: REGISTER DIRECT STOCK
    # ==================================================

    saved_private_documents = []

    try:

        selected_supplier = None

        # --------------------------------------------------
        # BASIC FORM VALUES
        # --------------------------------------------------

        product_mode = request.form.get(
            "product_mode",
            "existing"
        ).strip().lower()

        quantity = request.form.get(
            "quantity",
            type=int
        )

        if not quantity or quantity <= 0 or quantity > 100:
            raise ValueError(
                "Enter a valid handset quantity between 1 and 100."
            )

        def normalize(value):
            return str(value or "").strip().casefold()

        # --------------------------------------------------
        # SELECT BASE PRODUCT DETAILS
        # --------------------------------------------------

        if product_mode == "new":

            values = current_app.extensions["product_form_values"](
                require_used_grade=False
            )

            values["stock_quantity"] = 0
            values["track_imei"] = True

            # Defaults for fields not collected in Direct Stock
            values.setdefault("gst_percent", 0)
            values.setdefault("hsn_code", None)
            values.setdefault("warranty_months", 0)
            values.setdefault("reorder_level", 0)
            values.setdefault("is_active", True)

            base_values = values.copy()

        elif product_mode == "existing":

            product_id = request.form.get(
                "product_id",
                type=int
            )

            if not product_id:
                raise ValueError(
                    "Please select a product."
                )

            selected_product = Product.query.filter_by(
                id=product_id,
                is_active=True
            ).first()

            if not selected_product:
                raise ValueError(
                    "Selected product was not found."
                )

            if not selected_product.track_imei:
                raise ValueError(
                    "Selected product is not configured for IMEI tracking."
                )

            base_fields = [
                "name",
                "brand",
                "model",
                "category",
                "warranty_months",
                "hsn_code",
                "gst_percent",
                "reorder_level",
                "is_active",
            ]

            base_values = {
                field: getattr(selected_product, field)
                for field in base_fields
            }

            base_values.update({
                "product_code": selected_product.product_code,
                "barcode": selected_product.barcode,
                "variant": selected_product.variant,
                "color": selected_product.color,
                "condition": selected_product.condition,
                "used_grade": selected_product.used_grade,
            })

        else:

            raise ValueError(
                "Invalid product selection mode."
            )

        # --------------------------------------------------
        # READ INDIVIDUAL HANDSET DETAILS
        # --------------------------------------------------

        imei1_list = request.form.getlist("imei1[]")
        imei2_list = request.form.getlist("imei2[]")

        variant_list = request.form.getlist("variant[]")
        color_list = request.form.getlist("color[]")

        condition_list = request.form.getlist("condition[]")
        used_grade_list = request.form.getlist("used_grade[]")

        purchase_price_list = request.form.getlist(
            "purchase_price[]"
        )

        selling_price_list = request.form.getlist(
            "selling_price[]"
        )

        warranty_months_list = request.form.getlist(
            "warranty_months[]"
        )

        gst_percent_list = request.form.getlist(
            "gst_percent[]"
        )

        # Individual seller details
        seller_name_list = request.form.getlist(
            "seller_name[]"
        )

        seller_phone_list = request.form.getlist(
            "seller_phone[]"
        )

        seller_address_list = request.form.getlist(
            "seller_address[]"
        )

        # Individual proof documents
        id_proof_list = request.files.getlist(
            "id_proof[]"
        )

        handset_proof_list = request.files.getlist(
            "handset_proof[]"
        )

        handset_lists = [
            imei1_list,
            imei2_list,
            variant_list,
            color_list,
            condition_list,
            used_grade_list,
            purchase_price_list,
            selling_price_list,
            warranty_months_list,
            gst_percent_list,
            seller_name_list,
            seller_phone_list,
            seller_address_list,
            id_proof_list,
            handset_proof_list,
        ]

        if any(
            len(items) != quantity
            for items in handset_lists
        ):
            raise ValueError(
                "Handset details do not match the entered quantity. "
                "Please check the form."
            )

        # --------------------------------------------------
        # VALIDATE IMEI NUMBERS
        # --------------------------------------------------

        handset_imeis = []

        for index in range(quantity):

            imei1 = imei1_list[index].strip()
            imei2 = imei2_list[index].strip()

            if not imei1:
                raise ValueError(
                    f"IMEI 1 is required for handset {index + 1}."
                )

            if not imei1.isdigit() or len(imei1) != 15:
                raise ValueError(
                    f"Enter a valid 15-digit IMEI 1 for handset {index + 1}."
                )

            if imei2:

                if not imei2.isdigit() or len(imei2) != 15:
                    raise ValueError(
                        f"Enter a valid 15-digit IMEI 2 for handset {index + 1}."
                    )

                if imei1 == imei2:
                    raise ValueError(
                        f"IMEI 1 and IMEI 2 cannot be the same "
                        f"for handset {index + 1}."
                    )

            handset_imeis.append(imei1)

            if imei2:
                handset_imeis.append(imei2)

        if len(handset_imeis) != len(set(handset_imeis)):
            raise ValueError(
                "Duplicate IMEI numbers found in the submitted handsets."
            )

        # --------------------------------------------------
        # CHECK IMEI DUPLICATES IN DATABASE
        # --------------------------------------------------

        with db.session.no_autoflush:

            existing_imei = IMEIRecord.query.filter(
                db.or_(
                    IMEIRecord.imei1.in_(handset_imeis),
                    IMEIRecord.imei2.in_(handset_imeis)
                )
            ).first()

        if existing_imei:
            raise ValueError(
                "One or more IMEI numbers already exist in the inventory."
            )

        # --------------------------------------------------
        # PRODUCT MATCHING
        # --------------------------------------------------

        identity_fields = [
            "name",
            "brand",
            "model",
            "category",
            "variant",
            "color",
            "condition",
            "used_grade",
        ]

        product_cache = {}
        created_product_code_used = False

        def get_handset_product(handset):

            nonlocal created_product_code_used

            identity = {
                "name": base_values.get("name"),
                "brand": base_values.get("brand"),
                "model": base_values.get("model"),
                "category": base_values.get("category") or "Mobile",
                "variant": handset["variant"],
                "color": handset["color"],
                "condition": handset["condition"],
                "used_grade": handset["used_grade"],
            }

            identity_key = tuple(
                normalize(identity[field])
                for field in identity_fields
            )

            if identity_key in product_cache:
                return product_cache[identity_key]

            candidates = Product.query.filter_by(
                is_active=True,
                name=identity["name"]
            ).all()

            matched_product = None

            for candidate in candidates:

                if all(
                    normalize(getattr(candidate, field))
                    == normalize(identity[field])
                    for field in identity_fields
                ):

                    matched_product = candidate
                    break

            if matched_product:

                if not matched_product.track_imei:
                    raise ValueError(
                        f"{matched_product.name} is not configured "
                        "for IMEI tracking."
                    )

                product_cache[identity_key] = matched_product

                return matched_product

            # --------------------------------------------------
            # CREATE NEW PRODUCT VARIANT
            # --------------------------------------------------

            new_values = {
                field: base_values.get(field)
                for field in [
                    "name",
                    "brand",
                    "model",
                    "category",
                    "warranty_months",
                    "hsn_code",
                    "gst_percent",
                    "reorder_level",
                    "is_active",
                ]
            }

            new_values.update({

                "variant": identity["variant"],
                "color": identity["color"],
                "condition": identity["condition"],
                "used_grade": identity["used_grade"],

                "track_imei": True,
                "stock_quantity": 0,

                "cost_price": handset["purchase_price"],
                "selling_price": handset["selling_price"],

            })

            if product_mode == "new" and not created_product_code_used:

                new_values["product_code"] = base_values.get(
                    "product_code"
                )

                new_values["barcode"] = base_values.get(
                    "barcode"
                )

                if not new_values["product_code"]:
                    raise ValueError(
                        "Product code is required or must be generated "
                        "by your product form helper."
                    )

                if Product.query.filter_by(
                    product_code=new_values["product_code"]
                ).first():

                    raise ValueError(
                        "This product code already exists."
                    )

                if new_values["barcode"] and Product.query.filter_by(
                    barcode=new_values["barcode"]
                ).first():

                    raise ValueError(
                        "This barcode already exists."
                    )

                created_product_code_used = True

            else:

                while True:

                    generated_code = (
                        f"PRD-{datetime.now().strftime('%y%m%d%H%M%S')}-"
                        f"{os.urandom(2).hex().upper()}"
                    )

                    if not Product.query.filter_by(
                        product_code=generated_code
                    ).first():
                        break

                new_values["product_code"] = generated_code
                new_values["barcode"] = None

            new_product = Product(**new_values)

            db.session.add(new_product)
            db.session.flush()

            product_cache[identity_key] = new_product

            return new_product

        # --------------------------------------------------
        # CREATE INDIVIDUAL HANDSET RECORDS
        # --------------------------------------------------

        today = date.today()

        stock_movements = {}

        for index in range(quantity):

            handset = {

                "imei1": imei1_list[index].strip(),

                "imei2": (
                    imei2_list[index].strip() or None
                ),

                "variant": (
                    variant_list[index].strip()
                    or base_values.get("variant")
                ),

                "color": (
                    color_list[index].strip()
                    or base_values.get("color")
                ),

                "condition": (
                    condition_list[index].strip().title()
                    or "Used"
                ),

                "used_grade": (
                    used_grade_list[index].strip()
                    or None
                ),

            }

            # Direct Stock is only for Used handsets
            if handset["condition"] != "Used":

                raise ValueError(
                    f"Direct Stock Registration is only for Used handsets. "
                    f"Check handset {index + 1}."
                )

            if handset["used_grade"]:

                allowed_grades = {
                    "Excellent",
                    "Good",
                    "Fair",
                    "For Parts",
                }

                if handset["used_grade"] not in allowed_grades:

                    raise ValueError(
                        f"Choose a valid used grade for handset {index + 1}."
                    )

            # --------------------------------------------------
            # VALIDATE PRICES
            # --------------------------------------------------

            try:

                purchase_price = float(
                    purchase_price_list[index].strip() or 0
                )

                selling_price = float(
                    selling_price_list[index].strip() or 0
                )

            except (ValueError, TypeError, OverflowError):

                raise ValueError(
                    f"Enter valid prices for handset {index + 1}."
                )

            if (
                not math.isfinite(purchase_price)
                or not math.isfinite(selling_price)
                or purchase_price < 0
                or selling_price < 0
            ):

                raise ValueError(
                    f"Prices cannot be negative or invalid "
                    f"for handset {index + 1}."
                )

            handset["purchase_price"] = purchase_price
            handset["selling_price"] = selling_price

            # --------------------------------------------------
            # VALIDATE INDIVIDUAL WARRANTY AND GST
            # --------------------------------------------------

            try:
                warranty_months = int(
                    warranty_months_list[index].strip()
                )

                gst_percent = float(
                    gst_percent_list[index].strip()
                )

            except (ValueError, TypeError, OverflowError):
                raise ValueError(
                    f"Enter valid warranty and GST for handset {index + 1}."
                )

            if warranty_months < 0:
                raise ValueError(
                    f"Warranty must be 10 or 20 months for handset {index + 1}."
                )

            if not math.isfinite(gst_percent) or not 0 <= gst_percent <= 100:
                raise ValueError(
                    f"Enter valid GST between 0 and 100 for handset {index + 1}."
                )

            handset["warranty_months"] = warranty_months
            handset["gst_percent"] = gst_percent

            # --------------------------------------------------
            # GET OR CREATE PRODUCT
            # --------------------------------------------------

            product = get_handset_product(handset)

            # --------------------------------------------------
            # CREATE IMEI RECORD
            # --------------------------------------------------

            imei_record = IMEIRecord(

                product_id=product.id,

                imei1=handset["imei1"],
                imei2=handset["imei2"],

                supplier_id=selected_supplier.id if selected_supplier else None,
                purchase_item_id=None,

                source="direct",

                purchase_date=today,

                purchase_price=handset["purchase_price"],
                selling_price=handset["selling_price"],

                variant=handset["variant"],
                color=handset["color"],

                warranty_months=handset["warranty_months"],
                gst_percent=handset["gst_percent"],

                condition="Used",
                used_grade=handset["used_grade"],

                status="in_stock",

            )

            db.session.add(imei_record)

            # --------------------------------------------------
            # INDIVIDUAL SELLER DETAILS
            # --------------------------------------------------

            seller_name = seller_name_list[index].strip()
            seller_phone = seller_phone_list[index].strip()
            seller_address = seller_address_list[index].strip()

            if not seller_name or not seller_phone:

                raise ValueError(
                    f"Seller name and phone are required "
                    f"for handset {index + 1}."
                )

            if len(seller_name) > 150 or len(seller_phone) > 20:

                raise ValueError(
                    f"Seller details are too long "
                    f"for handset {index + 1}."
                )

            # Flush to obtain IMEI record ID
            db.session.flush()

            # --------------------------------------------------
            # SAVE PRIVATE PROOF DOCUMENTS
            # --------------------------------------------------

            id_proof_path = save_private_document(
                id_proof_list[index],
                folder="used_handsets",
            )

            handset_proof_path = save_private_document(
                handset_proof_list[index],
                folder="used_handsets",
            )

            if id_proof_path:
                saved_private_documents.append(id_proof_path)

            if handset_proof_path:
                saved_private_documents.append(handset_proof_path)

            # --------------------------------------------------
            # CREATE SELLER RECORD
            # --------------------------------------------------

            seller_record = UsedHandsetSeller(

                imei_record_id=imei_record.id,

                seller_name=seller_name,
                seller_phone=seller_phone,

                seller_address=seller_address or None,

                id_proof_path=id_proof_path,
                handset_proof_path=handset_proof_path,

            )

            db.session.add(seller_record)

            # --------------------------------------------------
            # TRACK STOCK MOVEMENT QUANTITY
            # --------------------------------------------------

            stock_movements[product.id] = (
                stock_movements.get(product.id, 0) + 1
            )

        # ==================================================
        # UPDATE PRODUCT STOCK AND CREATE MOVEMENTS
        # ==================================================

        for product_id, added_quantity in stock_movements.items():

            product = db.session.get(
                Product,
                product_id
            )

            if not product:

                raise ValueError(
                    "A product linked to the handset could not be found."
                )

            product.stock_quantity = (
                (product.stock_quantity or 0) + added_quantity
            )

            movement = StockMovement(

                product_id=product.id,

                user_id=current_user.id,

                movement_type="direct_stock",

                quantity_change=added_quantity,

                reference="DIRECT-STOCK",

                notes="Direct stock registration",

            )

            db.session.add(movement)

        # ==================================================
        # COMMIT
        # ==================================================

        db.session.commit()

        total_products = len(stock_movements)

        flash(
            f"Successfully registered {quantity} handset(s) "
            f"across {total_products} product variant(s).",
            "success"
        )

        return redirect(
            url_for("direct_stock_register")
        )

    # ==================================================
    # ERROR HANDLING
    # ==================================================

    except ValueError as e:

        db.session.rollback()

        for relative_path in saved_private_documents:
            try:
                full_path = (
                    current_app.config["PRIVATE_DOCUMENT_DIR"]
                    / relative_path
                )
                full_path.unlink(missing_ok=True)

            except OSError:
                current_app.logger.exception(
                    "Unable to remove uploaded document after rollback."
                )

        flash(
            str(e),
            "danger"
        )

    except Exception:

        db.session.rollback()

        for relative_path in saved_private_documents:

            try:
                full_path = (
                    current_app.config["PRIVATE_DOCUMENT_DIR"]
                    / relative_path
                )
                full_path.unlink(missing_ok=True)

            except OSError:
                current_app.logger.exception(
                    "Unable to remove uploaded document after rollback."
                )

        current_app.logger.exception(
            "Direct stock registration failed."
        )

        flash(
            "Unable to register stock. Please check the details and try again.",
            "danger"
        )

    return redirect(
        url_for("direct_stock_register")
    )

@app.route("/inventory/add-stock", methods=["GET", "POST"])
@login_required
def add_stock():

    products = (
        Product.query.filter_by(is_active=True)
        .order_by(Product.name.asc())
        .all()
    )

    suppliers = (
        Supplier.query.filter_by(is_active=True)
        .order_by(Supplier.name.asc())
        .all()
    )

    # -----------------------------------------
    # CHECK SUPPLIER-PRODUCT ASSOCIATION
    # -----------------------------------------

    def supplier_is_linked_to_product(product_id, supplier_id):

        direct_stock = IMEIRecord.query.filter_by(
            product_id=product_id,
            supplier_id=supplier_id,
            source="supplier"
        ).first()

        purchase_stock = (
            PurchaseItem.query
            .join(
                Purchase,
                PurchaseItem.purchase_id == Purchase.id
            )
            .filter(
                PurchaseItem.product_id == product_id,
                Purchase.supplier_id == supplier_id
            )
            .first()
        )

        return bool(direct_stock or purchase_stock)

    # -----------------------------------------
    # GET REQUEST
    # -----------------------------------------

    if request.method == "GET":

        selected_product_id = request.args.get(
            "product_id", type=int
        )

        requested_supplier_id = request.args.get(
            "supplier_id", type=int
        )

        selected_product = None
        preferred_supplier_id = None
        locked_supplier = None

        if selected_product_id:

            selected_product = Product.query.filter_by(
                id=selected_product_id,
                is_active=True
            ).first()

            if not selected_product:
                flash("Selected product was not found.", "danger")
                return redirect(url_for("products_list"))

            # Supplier passed from the Products row
            if requested_supplier_id:

                locked_supplier = Supplier.query.filter_by(
                    id=requested_supplier_id,
                    is_active=True
                ).first()

                if (
                    not locked_supplier
                    or not supplier_is_linked_to_product(
                        selected_product.id,
                        requested_supplier_id
                    )
                ):
                    flash(
                        "Selected supplier is not associated with this product.",
                        "danger"
                    )
                    return redirect(url_for("products_list"))

                preferred_supplier_id = locked_supplier.id

            else:

                # Preserve existing automatic supplier selection
                associated_supplier_ids = {
                    row[0]
                    for row in db.session.query(
                        IMEIRecord.supplier_id
                    ).filter(
                        IMEIRecord.product_id == selected_product.id,
                        IMEIRecord.supplier_id.isnot(None),
                        IMEIRecord.source == "supplier"
                    ).distinct().all()
                }

                purchase_supplier_ids = {
                    row[0]
                    for row in (
                        db.session.query(Purchase.supplier_id)
                        .join(
                            PurchaseItem,
                            PurchaseItem.purchase_id == Purchase.id
                        )
                        .filter(
                            PurchaseItem.product_id == selected_product.id,
                            Purchase.supplier_id.isnot(None)
                        )
                        .distinct()
                        .all()
                    )
                }

                all_supplier_ids = (
                    associated_supplier_ids | purchase_supplier_ids
                )

                if len(all_supplier_ids) == 1:
                    preferred_supplier_id = next(
                        iter(all_supplier_ids)
                    )

        return render_template(
            "inventory/add_stock.html",
            products=products,
            suppliers=suppliers,
            selected_product=selected_product,
            selected_product_id=selected_product_id,
            preferred_supplier_id=preferred_supplier_id,
            locked_supplier=locked_supplier,
            locked_supplier_id=preferred_supplier_id
        )

    # -----------------------------------------
    # POST REQUEST
    # -----------------------------------------

    # Preserve the row-specific supplier ID for redirects
    locked_supplier_id = request.args.get(
        "supplier_id", type=int
    )

    try:

        db.session.execute(text("BEGIN IMMEDIATE"))

        # -----------------------------------------
        # SELECT PRODUCT AND SUPPLIER
        # -----------------------------------------

        product_id = request.form.get("product_id", type=int)
        supplier_id = request.form.get("supplier_id", type=int)

        product = Product.query.filter_by(
            id=product_id,
            is_active=True
        ).first()

        if not product:
            raise ValueError("Please select a valid product.")

        # Validate and lock row-specific supplier
        if locked_supplier_id:

            if supplier_id != locked_supplier_id:
                raise ValueError(
                    "You cannot change the selected supplier."
                )

            if not supplier_is_linked_to_product(
                product.id,
                locked_supplier_id
            ):
                raise ValueError(
                    "Invalid supplier for this product."
                )

            supplier_id = locked_supplier_id

        supplier = Supplier.query.filter_by(
            id=supplier_id,
            is_active=True
        ).first()

        if not supplier:
            raise ValueError("Please select a valid supplier.")

        quantity = request.form.get("quantity", type=int)

        if not quantity or quantity <= 0:
            raise ValueError("Enter a valid stock quantity.")

        # -----------------------------------------
        # PRICE VALIDATION
        # -----------------------------------------

        def valid_price(value, label):

            try:
                price = float(value or 0)

            except (ValueError, TypeError, OverflowError):
                raise ValueError(
                    f"Enter a valid {label}."
                )

            if not math.isfinite(price) or price < 0:
                raise ValueError(
                    f"{label} cannot be negative or invalid."
                )

            return price

        def valid_gst(value, label):

            try:
                gst = float(value or 0)

            except (ValueError, TypeError, OverflowError):
                raise ValueError(f"Enter a valid {label}.")

            if not math.isfinite(gst) or not 0 <= gst <= 100:
                raise ValueError(
                    f"{label} must be between 0 and 100."
                )

            return gst

        def valid_warranty(value, label):

            try:
                warranty = int(value or 0)

            except (ValueError, TypeError, OverflowError):
                raise ValueError(f"Enter a valid {label} in whole months.")

            if warranty < 0:
                raise ValueError(
                    f"{label} cannot be negative."
                )

            return warranty

        today = date.today()
        added_imeis = []

        # -----------------------------------------
        # IMEI-TRACKED PRODUCTS
        # -----------------------------------------

        if product.track_imei:

            imei1_list = request.form.getlist("imei1[]")
            imei2_list = request.form.getlist("imei2[]")
            variant_list = request.form.getlist("variant[]")
            color_list = request.form.getlist("color[]")
            condition_list = request.form.getlist("condition[]")
            used_grade_list = request.form.getlist("used_grade[]")
            purchase_price_list = request.form.getlist("purchase_price[]")
            selling_price_list = request.form.getlist("selling_price[]")
            gst_list = request.form.getlist("gst_percent[]")
            warranty_list = request.form.getlist("warranty_months[]")

            handset_lists = [
                imei1_list,
                imei2_list,
                variant_list,
                color_list,
                condition_list,
                used_grade_list,
                purchase_price_list,
                selling_price_list,
                gst_list,
                warranty_list,
            ]

            if any(
                len(items) != quantity
                for items in handset_lists
            ):
                raise ValueError(
                    "Handset details do not match the entered quantity."
                )

            for index in range(quantity):

                imei1 = imei1_list[index].strip()
                imei2 = imei2_list[index].strip()

                if not imei1.isdigit() or len(imei1) != 15:
                    raise ValueError(
                        f"Enter a valid IMEI 1 for handset {index + 1}."
                    )

                if imei2:

                    if not imei2.isdigit() or len(imei2) != 15:
                        raise ValueError(
                            f"Enter a valid IMEI 2 for handset {index + 1}."
                        )

                    if imei1 == imei2:
                        raise ValueError(
                            f"IMEI 1 and IMEI 2 cannot be the same "
                            f"for handset {index + 1}."
                        )

                added_imeis.append(imei1)

                if imei2:
                    added_imeis.append(imei2)

                condition = condition_list[index].strip().title()

                if condition not in {"Used", "Sealed"}:
                    raise ValueError(
                        f"Invalid condition for handset {index + 1}."
                    )

                used_grade = used_grade_list[index].strip() or None

                if condition == "Used":

                    allowed_grades = {
                        "Excellent", "Good", "Fair", "For Parts"
                    }

                    if used_grade and used_grade not in allowed_grades:
                        raise ValueError(
                            f"Invalid used grade for handset {index + 1}."
                        )

                else:
                    used_grade = None

                purchase_price = valid_price(
                    purchase_price_list[index],
                    f"purchase price for handset {index + 1}"
                )

                selling_price = valid_price(
                    selling_price_list[index],
                    f"selling price for handset {index + 1}"
                )

                gst_percent = valid_gst(
                    gst_list[index],
                    f"GST for handset {index + 1}"
                )

                warranty_months = valid_warranty(
                    warranty_list[index],
                    f"Warranty for handset {index + 1}"
                )

                db.session.add(
                    IMEIRecord(
                        product_id=product.id,
                        imei1=imei1,
                        imei2=imei2 or None,
                        supplier_id=supplier.id,
                        purchase_item_id=None,
                        source="supplier",
                        purchase_date=today,
                        purchase_price=purchase_price,
                        selling_price=selling_price,
                        gst_percent=gst_percent,
                        warranty_months=warranty_months,
                        variant=variant_list[index].strip() or product.variant,
                        color=color_list[index].strip() or product.color,
                        condition=condition,
                        used_grade=used_grade,
                        status="in_stock",
                    )
                )

            if len(added_imeis) != len(set(added_imeis)):
                raise ValueError(
                    "Duplicate IMEI numbers found in the form."
                )

            with db.session.no_autoflush:

                existing_imei = IMEIRecord.query.filter(
                    db.or_(
                        IMEIRecord.imei1.in_(added_imeis),
                        IMEIRecord.imei2.in_(added_imeis)
                    )
                ).first()

            if existing_imei:
                raise ValueError(
                    "One or more IMEI numbers already exist in inventory."
                )

        # -----------------------------------------
        # NON-IMEI PRODUCTS
        # -----------------------------------------

        else:

            purchase_price = valid_price(
                request.form.get("purchase_price"),
                "purchase price"
            )

            selling_price = valid_price(
                request.form.get("selling_price"),
                "selling price"
            )

            gst_percent = valid_gst(
                request.form.get(
                    "gst_percent",
                    product.gst_percent or 0
                ),
                "GST"
            )

            warranty_months = valid_warranty(
                request.form.get(
                    "warranty_months",
                    product.warranty_months or 0
                ),
                "Warranty"
            )

            product.cost_price = purchase_price
            product.selling_price = selling_price

            product.gst_percent = gst_percent
            product.warranty_months = warranty_months

        # -----------------------------------------
        # UPDATE STOCK
        # -----------------------------------------

        if not product.track_imei:
            product.stock_quantity = (
            (product.stock_quantity or 0) + quantity
        )

        db.session.add(
            StockMovement(
                product_id=product.id,
                user_id=current_user.id,
                movement_type="supplier_stock",
                quantity_change=quantity,
                reference="SUPPLIER-STOCK",
                notes=f"Stock added from supplier: {supplier.name}",
            )
        )

        db.session.commit()

        flash(
            f"Successfully added {quantity} unit(s) "
            f"from supplier {supplier.name}.",
            "success"
        )

        return redirect(
            url_for(
                "add_stock",
                product_id=product.id,
                supplier_id=locked_supplier_id
            )
        )

    except ValueError as e:

        current_app.logger.warning(
        "Direct stock validation failed: %s", e
        )

        db.session.rollback()
        flash(str(e), "danger")

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "Supplier stock registration failed."
        )

        flash(
            "Unable to add supplier stock. Please check the details.",
            "danger"
        )

    return redirect(
        url_for(
            "add_stock",
            product_id=request.form.get("product_id", type=int),
            supplier_id=locked_supplier_id
        )
    )


@app.route(
    "/used-handset/document/<int:seller_id>/<string:document_type>"
)
@login_required
def view_used_handset_document(seller_id, document_type):

    seller = db.session.get(UsedHandsetSeller, seller_id)

    if not seller:
        abort(404)

    allowed_documents = {
        "id_proof": seller.id_proof_path,
        "handset_proof": seller.handset_proof_path,
    }

    document_path = allowed_documents.get(document_type)

    if not document_path:
        abort(404)

    private_dir = current_app.config["PRIVATE_DOCUMENT_DIR"]
    full_path = (private_dir / document_path).resolve()

    # Prevent access outside private document directory
    if not full_path.is_relative_to(private_dir.resolve()):
        abort(404)

    if not full_path.is_file():
        abort(404)

    return send_file(
        full_path,
        mimetype="image/jpeg" if full_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png",
        as_attachment=False,
    )


@app.route("/contacts")
@login_required
def contacts_dashboard():

    from sqlalchemy import or_
    from datetime import date, timedelta

    # --------------------------------------------------
    # FILTER VALUES
    # --------------------------------------------------

    search = request.args.get("search", "").strip()
    contact_type = request.args.get("contact_type", "all")
    product_id = request.args.get("product_id", "").strip()
    condition = request.args.get("condition", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    if contact_type not in ("all", "supplier", "seller"):
        contact_type = "all"

    try:
        selected_product_id = int(product_id) if product_id else None
    except (TypeError, ValueError):
        selected_product_id = None
        product_id = ""

    # --------------------------------------------------
    # SUPPLIER SEARCH
    # --------------------------------------------------

    supplier_query = Supplier.query.filter_by(is_active=True)

    if search:
        term = f"%{search}%"

        supplier_query = supplier_query.filter(
            or_(
                Supplier.name.ilike(term),
                Supplier.company_name.ilike(term),
                Supplier.contact_person.ilike(term),
                Supplier.phone.ilike(term),
                Supplier.email.ilike(term)
            )
        )

    matched_suppliers = (
        supplier_query
        .order_by(Supplier.name.asc())
        .all()
    )

    supplier_ids = [item.id for item in matched_suppliers]

    # --------------------------------------------------
    # USED HANDSET SELLER SEARCH
    # --------------------------------------------------

    seller_query = UsedHandsetSeller.query

    if search:
        term = f"%{search}%"

        seller_query = seller_query.filter(
            or_(
                UsedHandsetSeller.seller_name.ilike(term),
                UsedHandsetSeller.seller_phone.ilike(term),
                UsedHandsetSeller.seller_address.ilike(term)
            )
        )

    matched_sellers = (
        seller_query
        .order_by(UsedHandsetSeller.seller_name.asc())
        .all()
    )

    seller_ids = [item.id for item in matched_sellers]

    # --------------------------------------------------
    # PRODUCT LIST
    # --------------------------------------------------

    products = (
        Product.query
        .filter_by(is_active=True)
        .order_by(Product.name.asc())
        .all()
    )

    # --------------------------------------------------
    # HANDSET RECORDS
    # --------------------------------------------------

    records_query = (
        IMEIRecord.query
        .join(Product, IMEIRecord.product_id == Product.id)
        .outerjoin(
            UsedHandsetSeller,
            UsedHandsetSeller.imei_record_id == IMEIRecord.id
        )
    )

    # Contact type restriction
    if contact_type == "supplier":
        records_query = records_query.filter(
            IMEIRecord.supplier_id.isnot(None)
        )

    elif contact_type == "seller":
        records_query = records_query.filter(
            UsedHandsetSeller.id.isnot(None)
        )

    # Search across relevant handset/contact fields
    if search:
        term = f"%{search}%"

        search_conditions = [
            Product.name.ilike(term),
            Product.model.ilike(term),
            IMEIRecord.imei1.ilike(term),
            IMEIRecord.imei2.ilike(term)
        ]

        if supplier_ids and contact_type != "seller":
            search_conditions.append(
                IMEIRecord.supplier_id.in_(supplier_ids)
            )

        if seller_ids and contact_type != "supplier":
            search_conditions.append(
                UsedHandsetSeller.id.in_(seller_ids)
            )

        records_query = records_query.filter(
            or_(*search_conditions)
        )

    # Product filter
    if selected_product_id:
        records_query = records_query.filter(
            IMEIRecord.product_id == selected_product_id
        )

    # Condition filter
    if condition in ("Used", "Sealed"):
        records_query = records_query.filter(
            IMEIRecord.condition == condition
        )

    # Start date
    if date_from:
        try:
            records_query = records_query.filter(
                IMEIRecord.purchase_date >= date.fromisoformat(date_from)
            )
        except ValueError:
            pass

    # End date (inclusive)
    if date_to:
        try:
            records_query = records_query.filter(
                IMEIRecord.purchase_date <= date.fromisoformat(date_to)
            )
        except ValueError:
            pass

    records = (
        records_query
        .order_by(
            IMEIRecord.purchase_date.desc(),
            IMEIRecord.id.desc()
        )
        .all()
    )

    # --------------------------------------------------
    # SUPPLIER PURCHASE HISTORY
    # --------------------------------------------------

    purchase_items_query = (
        PurchaseItem.query
        .join(Purchase, PurchaseItem.purchase_id == Purchase.id)
        .join(Product, PurchaseItem.product_id == Product.id)
    )

    # Seller-only view must not show supplier purchases
    if contact_type == "seller":
        purchase_items_query = purchase_items_query.filter(
            db.false()
        )

    # Search supplier, invoice and product
    if search and contact_type != "seller":
        term = f"%{search}%"

        purchase_search_conditions = [
            Purchase.invoice_number.ilike(term),
            Product.name.ilike(term),
            Product.model.ilike(term)
        ]

        if supplier_ids:
            purchase_search_conditions.append(
                Purchase.supplier_id.in_(supplier_ids)
            )

        purchase_items_query = purchase_items_query.filter(
            or_(*purchase_search_conditions)
        )

    # Product filter
    if selected_product_id:
        purchase_items_query = purchase_items_query.filter(
            PurchaseItem.product_id == selected_product_id
        )

    # Purchase date start
    if date_from:
        try:
            purchase_items_query = purchase_items_query.filter(
                Purchase.purchase_date >= date.fromisoformat(date_from)
            )
        except ValueError:
            pass

    # Purchase date end (inclusive)
    if date_to:
        try:
            purchase_items_query = purchase_items_query.filter(
                Purchase.purchase_date < (
                    date.fromisoformat(date_to) + timedelta(days=1)
                )
            )
        except ValueError:
            pass

    purchase_items = (
        purchase_items_query
        .order_by(
            Purchase.purchase_date.desc(),
            Purchase.id.desc()
        )
        .all()
    )

    # --------------------------------------------------
    # FINAL CONTACT LISTS
    # --------------------------------------------------

    suppliers = (
        matched_suppliers
        if contact_type != "seller"
        else []
    )

    sellers = (
        matched_sellers
        if contact_type != "supplier"
        else []
    )


    # --------------------------------------------------
    # USED SELLER ACCOUNT SUMMARY
    # --------------------------------------------------

    seller_accounts = []

    if contact_type != "supplier":

        # Get unique seller phone numbers
        seller_phones = list(dict.fromkeys(
            seller.seller_phone.strip()
            for seller in sellers
            if seller.seller_phone
        ))

        for phone in seller_phones:

            # Get all handset records belonging to this seller
            seller_records = (
                UsedHandsetSeller.query
                .filter_by(seller_phone=phone)
                .all()
            )

            total_purchase = sum(
                float(item.imei_record.purchase_price or 0)
                for item in seller_records
                if item.imei_record
            )

            # Total payments made to this seller
            total_paid = (
                db.session.query(
                    db.func.coalesce(
                        db.func.sum(UsedSellerPayment.amount), 0
                    )
                )
                .filter_by(seller_phone=phone)
                .scalar()
            )

            total_paid = float(total_paid or 0)


        # Payment history for this seller
            payment_history = (
                UsedSellerPayment.query
            .filter_by(seller_phone=phone)
            .order_by(
                UsedSellerPayment.payment_date.desc(),
                UsedSellerPayment.id.desc()
            )
            .all()
            )


            seller_accounts.append({
                "seller_name": seller_records[0].seller_name,
                "seller_phone": phone,
                "handset_count": len(seller_records),
                "total_purchase": total_purchase,
                "total_paid": total_paid,
                "outstanding": max(0, total_purchase - total_paid),
                "payment_history": payment_history
            })


    # --------------------------------------------------
    # RENDER TEMPLATE
    # --------------------------------------------------

    return render_template(
        "contact/index.html",
        today_date=date.today().isoformat(),
        suppliers=suppliers,
        sellers=sellers,
        products=products,
        records=records,
        purchase_items=purchase_items,
        search=search,
        contact_type=contact_type,
        product_id=product_id,
        condition=condition,
        date_from=date_from,
        date_to=date_to,
        seller_accounts=seller_accounts,
    )


@app.route("/used-seller-payment", methods=["POST"])
@login_required
def used_seller_payment():

    from decimal import Decimal, InvalidOperation

    seller_phone = request.form.get("seller_phone", "").strip()
    seller_name = request.form.get("seller_name", "").strip()

    try:
        amount = Decimal(request.form.get("amount", "").strip())
        payment_date = date.fromisoformat(
            request.form.get("payment_date", "").strip()
        )
    except (InvalidOperation, ValueError):
        flash("Please enter a valid payment amount and date.", "danger")
        return redirect(url_for("contacts_dashboard"))

    if not seller_phone or not seller_name:
        flash("Seller details are missing.", "danger")
        return redirect(url_for("contacts_dashboard"))

    if not amount.is_finite() or amount <= 0:
        flash("Payment amount must be greater than zero.", "danger")
        return redirect(url_for("contacts_dashboard"))

    allowed_methods = {
        "cash", "upi", "bank_transfer", "card", "other"
    }

    payment_method = request.form.get("payment_method", "cash").strip()

    if payment_method not in allowed_methods:
        flash("Invalid payment method.", "danger")
        return redirect(url_for("contacts_dashboard"))

    try:
        # Calculate this seller's total used handset purchases
        seller_records = UsedHandsetSeller.query.filter_by(
            seller_phone=seller_phone
        ).all()

        total_purchase = sum(
            Decimal(str(item.imei_record.purchase_price or 0))
            for item in seller_records
            if item.imei_record
        )

        total_paid = (
            db.session.query(
                db.func.coalesce(
                    db.func.sum(UsedSellerPayment.amount), 0
                )
            )
            .filter_by(seller_phone=seller_phone)
            .scalar()
        )

        outstanding = total_purchase - Decimal(str(total_paid or 0))

        if amount > outstanding:
            flash(
                "Payment cannot exceed the outstanding balance.",
                "danger"
            )
            return redirect(url_for("contacts_dashboard"))

        payment = UsedSellerPayment(
            seller_phone=seller_phone,
            seller_name=seller_name,
            amount=float(amount),
            payment_date=payment_date,
            payment_method=payment_method,
            reference=request.form.get("reference", "").strip(),
            notes=request.form.get("notes", "").strip()
        )

        db.session.add(payment)
        db.session.commit()

        flash("Seller payment recorded successfully.", "success")

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Used seller payment failed")
        flash("Could not record seller payment.", "danger")

    return redirect(url_for("contacts_dashboard"))


@app.route("/billing/payment/<int:payment_id>/receipt")
@login_required
def payment_receipt(payment_id):
    payment = db.session.get(Payment, payment_id)

    if payment is None or payment.sale_id is None:
        flash("Payment record not found.", "danger")
        return redirect(url_for("billing_list"))

    sale = payment.sale

    if sale is None:
        flash("Associated invoice not found.", "danger")
        return redirect(url_for("billing_list"))

    if payment.status != "completed":
        flash("Receipt is available only for completed payments.", "warning")
        return redirect(url_for("billing_detail", sale_id=sale.id))

    payments = (
        Payment.query
        .filter(
            Payment.sale_id == sale.id,
            Payment.status == "completed",
            Payment.id < payment.id,
        )
        .all()
    )

    previous_paid = sum(
        float(p.amount or 0) for p in payments
    )

    balance_before = max(
        float(sale.total_amount or 0) - previous_paid,
        0,
    )

    balance_after = max(
        balance_before - float(payment.amount or 0),
        0,
    )

    store = StoreSettings.query.first()

    return render_template(
        "billing/payment_receipt.html",
        payment=payment,
        sale=sale,
        store=store,
        balance_before=round(balance_before, 2),
        balance_after=round(balance_after, 2),
    )


# Keep these imports at the top of app.py
import base64
import threading
import urllib.request
import time as time_module
import webview

app = create_app()

# --------------------------------------------------
# REAL PRODUCT LICENSING
# License files are stored in the persistent per-user DATA_DIR.
# --------------------------------------------------
from licensing import LicenseManager

license_manager = LicenseManager(
    product_id="MOBILE-APP-STORE",
    display_name="MobileStore ERP",
    data_dir=str(DATA_DIR),
)
license_manager.install(app)

# # The activation endpoint receives JSON from the activation page. Exempt only
# # this endpoint from Flask-WTF CSRF because the license module uses its own
# # machine-bound RSA validation and does not use the ERP form session.
csrf.exempt(app.view_functions["license_activate"])

# # A new token per Python process lets the browser show the expiry reminder
# # once per application opening, even if the user navigates between ERP pages.
app.config["LICENSE_STARTUP_TOKEN"] = secrets.token_urlsafe(16)

@app.before_request
def enforce_full_license_access():
    endpoint = request.endpoint or ""
    allowed_endpoints = {
        "license_activation_page", "license_status", "license_activate",
        "license_machine_id", "get_session_count", "static"
    }
    if endpoint in allowed_endpoints or endpoint.startswith("static"):
        return None

    status = license_manager.cached_status()
    if status.get("valid"):
        return None

    if request.path.startswith("/api/"):
        return jsonify({
            "error": "license_invalid",
            "message": status.get("message", "License is not valid."),
            "expired": bool(status.get("expired")),
        }), 403

    return redirect(url_for("license_activation_page"))

@app.route("/activate")
def license_activation_page():
    status = license_manager.check_license_status()
    return render_template("license_activation.html", license_status=status)


app.add_url_rule(
    "/service-stock",
    endpoint="service_stock",
    view_func=login_required(service_stock),
    methods=["GET", "POST"],
)


class Api:
    def save_pdf(self, pdf_base64, filename):
        try:
            print("[PDF] Save handler called")

            pdf_bytes = base64.b64decode(pdf_base64, validate=True)
            print(f"[PDF] Decoded {len(pdf_bytes)} bytes")

            # Open PDF save dialog
            save_path = window.create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=filename,
                file_types=("PDF Files (*.pdf)",),
            )

            print(f"[PDF] Save dialog result: {save_path!r}")

            # Handle cancellation
            if not save_path:
                print("[PDF] Save cancelled by user")
                return {
                    "success": False,
                    "message": "Save cancelled"
                }

            # Handle list/tuple return values
            if isinstance(save_path, (list, tuple)):
                save_path = save_path[0]

            # Ensure PDF extension
            if not save_path.lower().endswith(".pdf"):
                save_path += ".pdf"

            # Save PDF
            with open(save_path, "wb") as pdf_file:
                pdf_file.write(pdf_bytes)

            print(f"[PDF] PDF saved successfully: {save_path}")

            return {
                "success": True,
                "path": save_path
            }

        except Exception as e:
            print(f"[PDF] Save error: {e}")

            return {
                "success": False,
                "message": str(e)
            }





def run_flask(flask_app, startup_errors):
    try:
        flask_app.run(
            host="127.0.0.1",
            port=5001,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    except Exception as exc:
        startup_errors.append(str(exc))
        flask_app.logger.exception("Flask server failed to start.")

# from datetime import datetime

def record_app_launch():
    with app.app_context():
        settings = db.session.get(StoreSettings, 1)

        if settings is None:
            settings = StoreSettings(id=1)
            db.session.add(settings)

        settings.app_launch_count = (
            settings.app_launch_count or 0
        ) + 1

        settings.app_last_opened = datetime.now()

        db.session.commit()

if __name__ == "__main__":
    try:
        # Record a licensed application session at each successful launch.
        license_manager.startup()

        # Capture Flask startup errors
        startup_errors = []

        flask_thread = threading.Thread(
            target=run_flask,
            args=(app, startup_errors),
            daemon=True,
        )
        flask_thread.start()

        # Wait for Flask server
        server_ready = False

        for _ in range(50):

            if startup_errors:
                break

            if not flask_thread.is_alive():
                break

            
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:5001",
                    timeout=1,
                ):
                    server_ready = True
                    break

            except urllib.error.HTTPError:
                # HTTP 404 still confirms that Flask is responding.
                server_ready = True
                break

            except Exception:
                time_module.sleep(0.2)


        if not server_ready:
            if startup_errors:
                raise RuntimeError(
                    f"Flask startup failed: {startup_errors[0]}"
                )

            raise RuntimeError(
                "Flask server did not respond on port 5001."
            )

        # Initialize PDF API
        api = Api()

        # Create desktop window
        window = webview.create_window(
            "MobileStore ERP",
            "http://127.0.0.1:5001",
            width=1200,
            height=800,
            js_api=api,
        )

        # Count one successful application start after the server and window are ready.
        record_app_launch()

        # Start Edge Chromium
        webview.start(gui="edgechromium")

    except Exception:
        app.logger.exception("MobileStore ERP startup failed.")

        print(
            "MobileStore ERP could not start. "
            "Please check the application log."
        )

        print(
            "Log location: "
            f"{DATA_DIR / 'logs' / 'app.log'}"
        )

        raise


# if __name__ == "__main__":
#     record_app_launch()
#     flask_thread = threading.Thread(
#         target=run_flask,
#         args=(app,),
#         daemon=True,
#     )
#     flask_thread.start()

#     server_ready = False

#     for _ in range(50):
#         try:
#             with urllib.request.urlopen(
#                 "http://127.0.0.1:5001",
#                 timeout=1,
#             ):
#                 server_ready = True
#                 break
#         except Exception:
#             time_module.sleep(0.2)

#     if not server_ready:
#         raise RuntimeError(
#             "Flask server failed to start on port 5001."
#         )

#     api = Api()

#     window = webview.create_window(
#         "MobileStore ERP",
#         "http://127.0.0.1:5001",
#         width=1200,
#         height=800,
#         js_api=api,
#     )

#     webview.start(gui="edgechromium")
