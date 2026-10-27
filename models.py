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
    invoice_number = db.Column(db.String(80), unique=True, nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey("suppliers.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    purchase_date = db.Column(db.DateTime(timezone=True), default=utc_now)
    total_amount = db.Column(db.Float, default=0, nullable=False)
    status = db.Column(db.String(30), default="received", nullable=False)
    supplier = db.relationship("Supplier")
    user = db.relationship("User")


class PurchaseItem(db.Model):
    __tablename__ = "purchase_items"
    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_cost = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)
    purchase = db.relationship(
        "Purchase", backref=db.backref("items", cascade="all, delete-orphan")
    )
    product = db.relationship("Product")


class Sale(TimestampMixin, db.Model):
    __tablename__ = "sales"
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(80), unique=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    sale_date = db.Column(db.DateTime(timezone=True), default=utc_now)
    subtotal = db.Column(db.Float, default=0, nullable=False)
    tax_amount = db.Column(db.Float, default=0, nullable=False)
    discount_amount = db.Column(db.Float, default=0, nullable=False)
    total_amount = db.Column(db.Float, default=0, nullable=False)
    status = db.Column(db.String(30), default="completed", nullable=False)
    payments = db.relationship(
        "Payment",
        back_populates="sale",
        lazy="select",
        cascade="all, delete-orphan",
    )
    customer = db.relationship("Customer")
    user = db.relationship("User")


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
    purchase = db.relationship("Purchase")


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
