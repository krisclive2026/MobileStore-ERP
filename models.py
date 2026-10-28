from datetime import datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def utc_now():
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(150), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="cashier")
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "role": self.role,
            "is_active": self.is_active,
        }


class Product(TimestampMixin, db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)

    # Product identification
    product_code = db.Column(db.String(50), unique=True, nullable=False)
    barcode = db.Column(db.String(100), unique=True, nullable=True)

    # Product details
    name = db.Column(db.String(150), nullable=False, index=True)
    brand = db.Column(db.String(100))
    model = db.Column(db.String(100))
    category = db.Column(db.String(80), default="Mobile")

    # Variant details
    variant = db.Column(db.String(100))
    color = db.Column(db.String(50))

    # Sealed or Used
    condition = db.Column(db.String(30), nullable=False, default="Sealed")
    used_grade = db.Column(db.String(30))
    warranty_months = db.Column(db.Integer, default=0)

    # Tax and pricing
    hsn_code = db.Column(db.String(30), nullable=True)
    gst_percent = db.Column(db.Float, default=0, nullable=False)
    cost_price = db.Column(db.Float, default=0, nullable=False)
    selling_price = db.Column(db.Float, default=0, nullable=False)

    # Stock quantity
    stock_quantity = db.Column(db.Integer, default=0, nullable=False)
    reorder_level = db.Column(db.Integer, default=5, nullable=False)

    # Individual handset tracking
    track_imei = db.Column(db.Boolean, default=True, nullable=False)

    # Product activation status
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # Relationship with individual handset IMEI records
    imei_records = db.relationship("IMEIRecord", back_populates="product", lazy=True)

    # Convert product object to dictionary
    def to_dict(self):
        return {
            "id": self.id,
            "product_code": self.product_code,
            "barcode": self.barcode,
            "name": self.name,
            "brand": self.brand,
            "model": self.model,
            "category": self.category,
            "variant": self.variant,
            "color": self.color,
            "condition": self.condition,
            "used_grade": self.used_grade,
            "warranty_months": self.warranty_months,
            "hsn_code": self.hsn_code,
            "gst_percent": self.gst_percent,
            "cost_price": self.cost_price,
            "selling_price": self.selling_price,
            "stock_quantity": self.stock_quantity,
            "reorder_level": self.reorder_level,
            "track_imei": self.track_imei,
            "is_active": self.is_active,
        }


class IMEIRecord(TimestampMixin, db.Model):
    __tablename__ = "imei_records"
    id = db.Column(db.Integer, primary_key=True)
    
    product_id = db.Column(
    db.Integer,
    db.ForeignKey("products.id"),
    nullable=False,
    index=True
)
    
     # First IMEI — required
    imei1 = db.Column(
        db.String(15),
        unique=True,
        nullable=False,
        index=True
    )

    # Second IMEI — optional
    imei2 = db.Column(
        db.String(15),
        unique=True,
        nullable=True,
        index=True
    )
    
    status = db.Column(db.String(20), default="in_stock", nullable=False)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"))
    product = db.relationship("Product", back_populates="imei_records")


class Customer(TimestampMixin, db.Model):
    __tablename__ = "customers"
    id = db.Column(db.Integer, primary_key=True)
    customer_code = db.Column(
        db.String(20),
        unique=True,
        nullable=True,
        index=True
    )
    name = db.Column(db.String(150), nullable=False, index=True)
    phone = db.Column(db.String(20), index=True)
    email = db.Column(db.String(150))
    address = db.Column(db.Text)
    gst_number = db.Column(db.String(30))
    

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "address": self.address,
            "gst_number": self.gst_number,
            "customer_code": self.customer_code,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }




class Supplier(TimestampMixin, db.Model):
    __tablename__ = "suppliers"

    id = db.Column(db.Integer, primary_key=True)

    # Basic information
    name = db.Column(db.String(150), nullable=False, index=True)
    company_name = db.Column(db.String(150))
    contact_person = db.Column(db.String(100))

    # Contact information
    phone = db.Column(db.String(20))
    mobile_number_2 = db.Column(db.String(20))
    email = db.Column(db.String(150))
    address = db.Column(db.Text)

    # Business information
    gst_number = db.Column(db.String(30))
    supplier_category = db.Column(db.String(80))
    payment_terms = db.Column(db.String(50))
    delivery_lead_time = db.Column(db.Integer, default=0)

    # Additional information
    notes = db.Column(db.Text)
    is_active = db.Column(
        db.Boolean,
        default=True,
        nullable=False,
    )


class Purchase(TimestampMixin, db.Model):
    __tablename__ = "purchases"

    id = db.Column(db.Integer, primary_key=True)

    invoice_number = db.Column(
        db.String(80), unique=True, nullable=False
    )

    supplier_id = db.Column(
        db.Integer, db.ForeignKey("suppliers.id"),
        nullable=False
    )

    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"),
        nullable=False
    )

    purchase_date = db.Column(
        db.DateTime(timezone=True), default=utc_now, nullable=False
    )
    expected_delivery_date = db.Column(db.Date, nullable=True)
    actual_delivery_date = db.Column(db.Date, nullable=True)
    payment_due_date = db.Column(db.Date, nullable=True)

    total_amount = db.Column(
        db.Float, default=0, nullable=False
    )

    status = db.Column(
        db.String(30), default="ordered", nullable=False
    )

    supplier = db.relationship("Supplier")
    user = db.relationship("User")

    payments = db.relationship(
        "Payment",
        back_populates="purchase",
        foreign_keys="Payment.purchase_id",
        order_by="Payment.created_at",
    )

    @property
    def amount_paid(self):
        return round(
            sum(
                payment.amount or 0
                for payment in self.payments
                if payment.status == "completed"
            ),
            2,
        )

    @property
    def balance_due(self):
        return round(
            max(0, (self.total_amount or 0) - self.amount_paid),
            2,
        )

    @property
    def payment_status(self):
        if self.balance_due <= 0:
            return "Paid"
        if self.amount_paid > 0:
            return "Partially Paid"
        return "Unpaid"


class PurchaseItem(db.Model):
    __tablename__ = "purchase_items"

    id = db.Column(db.Integer, primary_key=True)

    purchase_id = db.Column(
        db.Integer, db.ForeignKey("purchases.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )

    quantity = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)

    purchase = db.relationship(
        "Purchase",
        backref=db.backref("items", cascade="all, delete-orphan"),
    )
    product = db.relationship("Product")
    

class Sale(TimestampMixin, db.Model):
    __tablename__ = "sales"

    id = db.Column(db.Integer, primary_key=True)

    invoice_number = db.Column(
        db.String(80), unique=True, nullable=False
    )

    customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.id"), nullable=True
    )

    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False
    )

    sale_date = db.Column(
        db.DateTime(timezone=True), default=utc_now, nullable=False
    )

    subtotal = db.Column(db.Float, default=0, nullable=False)
    tax_amount = db.Column(db.Float, default=0, nullable=False)
    discount_amount = db.Column(db.Float, default=0, nullable=False)
    total_amount = db.Column(db.Float, default=0, nullable=False)

    status = db.Column(
        db.String(30), default="completed", nullable=False
    )

    customer = db.relationship("Customer")
    user = db.relationship("User")

    payments = db.relationship(
        "Payment",
        back_populates="sale",
        foreign_keys="Payment.sale_id",
    )


class SaleItem(db.Model):
    __tablename__ = "sale_items"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    tax_amount = db.Column(db.Float, default=0, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)
    sale = db.relationship(
        "Sale", backref=db.backref("items", cascade="all, delete-orphan")
    )
    product = db.relationship("Product")


class Payment(TimestampMixin, db.Model):
    __tablename__ = "payments"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"))
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"))
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(30), nullable=False, default="cash")
    reference = db.Column(db.String(100))
    status = db.Column(db.String(30), default="completed", nullable=False)
    sale = db.relationship("Sale", back_populates="payments")
    purchase = db.relationship(
    "Purchase",
    back_populates="payments",
    foreign_keys=[purchase_id],
)


class StockMovement(TimestampMixin, db.Model):
    __tablename__ = "stock_movements"
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    movement_type = db.Column(db.String(30), nullable=False)
    quantity_change = db.Column(db.Integer, nullable=False)
    reference = db.Column(db.String(100))
    notes = db.Column(db.Text)
    product = db.relationship("Product")
    user = db.relationship("User")


class Expense(TimestampMixin, db.Model):
    __tablename__ = "expenses"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(80))
    amount = db.Column(db.Float, nullable=False)
    expense_date = db.Column(db.DateTime(timezone=True), default=utc_now)
    notes = db.Column(db.Text)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user = db.relationship("User")


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    action = db.Column(db.String(100), nullable=False)
    entity = db.Column(db.String(100))
    entity_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now)
    user = db.relationship("User")


class StoreSettings(TimestampMixin, db.Model):
    __tablename__ = "store_settings"

    id = db.Column(db.Integer, primary_key=True)

    store_name = db.Column(db.String(150), nullable=False, default="My Store")
    logo_filename = db.Column(db.String(255))
    address = db.Column(db.Text)
    phone = db.Column(db.String(30))
    gstin = db.Column(db.String(30))

    paper_size = db.Column(db.String(20), nullable=False, default="A4")
    orientation = db.Column(db.String(20), nullable=False, default="portrait")

    custom_width_mm = db.Column(db.Float, default=100)
    custom_height_mm = db.Column(db.Float, default=150)

    footer_text = db.Column(db.String(500))
    
    opening_time = db.Column(db.String(20))
    closing_time = db.Column(db.String(20))

    invoice_prefix = db.Column(db.String(20), default="INV")
    show_customer_id = db.Column(db.Boolean, default=True)
    show_gstin = db.Column(db.Boolean, default=True)
    show_tax_breakdown = db.Column(db.Boolean, default=True)
    show_emi_details = db.Column(db.Boolean, default=True)

    margin_top_mm = db.Column(db.Float, default=10)
    margin_bottom_mm = db.Column(db.Float, default=10)
    font_size = db.Column(db.Float, default=10)

    instagram_url = db.Column(db.String(255))
    facebook_url = db.Column(db.String(255))
    whatsapp_url = db.Column(db.String(255))


class ServiceJob(TimestampMixin, db.Model):
    __tablename__ = "service_jobs"

    id = db.Column(db.Integer, primary_key=True)

    # Service identification
    job_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    service_type = db.Column(db.String(50), nullable=False, default="Repair")
    device_brand = db.Column(db.String(100), nullable=False)
    device_model = db.Column(db.String(100), nullable=False)
    imei_number = db.Column(db.String(30), nullable=True, index=True)

    # Customer information
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey("customers.id"),
        nullable=True
    )
    customer_name = db.Column(db.String(120), nullable=False)
    customer_phone = db.Column(db.String(20), nullable=False)

    # Repair information
    problem_description = db.Column(db.Text, nullable=False)
    diagnosis = db.Column(db.Text, nullable=True)
    solution = db.Column(db.Text, nullable=True)
    accessories_received = db.Column(db.String(255), nullable=True)
    technician_name = db.Column(db.String(120), nullable=True)

    # Status tracking
    status = db.Column(
        db.String(30),
        nullable=False,
        default="Received",
        index=True
    )
    priority = db.Column(
        db.String(20),
        nullable=False,
        default="Normal"
    )

    received_date = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now
    )
    estimated_completion = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_date = db.Column(db.DateTime(timezone=True), nullable=True)
    delivered_date = db.Column(db.DateTime(timezone=True), nullable=True)

    # Charges
    service_charge = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )
    parts_charge = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )
    discount_amount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )
    tax_amount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )
    total_amount = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )

    # Payment tracking
    amount_paid = db.Column(
        db.Numeric(12, 2),
        nullable=False,
        default=0
    )
    payment_status = db.Column(
        db.String(20),
        nullable=False,
        default="Unpaid"
    )
    payment_method = db.Column(db.String(30), nullable=True)

    # Additional information
    warranty_days = db.Column(db.Integer, nullable=False, default=0)
    warranty_expiry = db.Column(db.DateTime(timezone=True), nullable=True)
    internal_notes = db.Column(db.Text, nullable=True)
    customer_notes = db.Column(db.Text, nullable=True)

    # User who created the service job
    created_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True
    )

    customer = db.relationship("Customer", foreign_keys=[customer_id])
    creator = db.relationship("User", foreign_keys=[created_by])

    parts = db.relationship(
        "ServicePart",
        back_populates="service_job",
        cascade="all, delete-orphan"
    )

    history = db.relationship(
        "ServiceHistory",
        back_populates="service_job",
        cascade="all, delete-orphan",
        order_by="ServiceHistory.created_at.desc()"
    )

    @property
    def balance_due(self):
        return max(
            (self.total_amount or 0) - (self.amount_paid or 0),
            0
        )

    @property
    def is_fully_paid(self):
        return self.balance_due == 0

    def update_payment_status(self):
        paid = self.amount_paid or 0
        total = self.total_amount or 0

        if paid <= 0:
            self.payment_status = "Unpaid"
        elif paid < total:
            self.payment_status = "Partially Paid"
        else:
            self.payment_status = "Paid"


class ServicePart(TimestampMixin, db.Model):
    __tablename__ = "service_parts"

    id = db.Column(db.Integer, primary_key=True)

    service_job_id = db.Column(
        db.Integer,
        db.ForeignKey("service_jobs.id"),
        nullable=False,
        index=True
    )

    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=True
    )

    part_name = db.Column(db.String(150), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    service_job = db.relationship(
        "ServiceJob",
        back_populates="parts"
    )
    product = db.relationship("Product")


class ServiceHistory(TimestampMixin, db.Model):
    __tablename__ = "service_history"

    id = db.Column(db.Integer, primary_key=True)

    service_job_id = db.Column(
        db.Integer,
        db.ForeignKey("service_jobs.id"),
        nullable=False,
        index=True
    )

    old_status = db.Column(db.String(30), nullable=True)
    new_status = db.Column(db.String(30), nullable=False)
    remarks = db.Column(db.Text, nullable=True)

    updated_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=True
    )

    service_job = db.relationship(
        "ServiceJob",
        back_populates="history"
    )
    user = db.relationship("User")
