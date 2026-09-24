import os
import math
from datetime import datetime, timezone

import socketio
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

# =========================
# Database
# =========================

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./delivery_tracking.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {}

if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine)

Base = declarative_base()


# =========================
# Database Models
# =========================

class Rider(Base):
    __tablename__ = "riders"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    available = Column(Boolean, default=True)


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)

    pickup_address = Column(String, nullable=False)
    drop_address = Column(String, nullable=False)

    pickup_latitude = Column(Float, nullable=False)
    pickup_longitude = Column(Float, nullable=False)

    drop_latitude = Column(Float, nullable=False)
    drop_longitude = Column(Float, nullable=False)

    rider_id = Column(Integer, nullable=True)

    status = Column(String, default="Placed")

    rider_latitude = Column(Float, nullable=True)
    rider_longitude = Column(Float, nullable=True)

    distance_km = Column(Float, nullable=True)
    eta_minutes = Column(Integer, nullable=True)

    arriving_soon_notified = Column(Boolean, default=False)

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc)
    )


Base.metadata.create_all(bind=engine)


# =========================
# FastAPI
# =========================

app = FastAPI(title="Real-Time Delivery Tracking API")

# IMPORTANT: allow_credentials must be False when allow_origins is "*" (a
# wildcard). Browsers reject the combination of wildcard origins +
# credentials=True as a security rule, which silently breaks every
# fetch/axios call from the frontend with a CORS error in the console.
# This was the actual cause of the "fetch error" — not the rider logic.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# Socket.IO
# =========================

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*"
)

socket_app = socketio.ASGIApp(sio, app)


# =========================
# Schemas
# =========================

class RiderCreate(BaseModel):
    name: str
    phone: str | None = None
    latitude: float
    longitude: float


class OrderCreate(BaseModel):
    pickup_address: str
    drop_address: str

    pickup_latitude: float
    pickup_longitude: float

    drop_latitude: float
    drop_longitude: float


class RiderLocationUpdate(BaseModel):
    rider_id: int
    order_id: int
    latitude: float
    longitude: float


class StatusUpdate(BaseModel):
    status: str


# =========================
# Constants
# =========================

VALID_STATUSES = [
    "Placed",
    "Rider Assigned",
    "Picked Up",
    "On the Way",
    "Delivered",
]


# =========================
# Helper Functions
# =========================

def haversine(lat1, lon1, lat2, lon2):
    """
    Calculate distance between two coordinates in kilometers.
    """

    earth_radius = 6371

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)

    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return earth_radius * c


def calculate_eta(distance_km):
    """
    Simple ETA calculation.
    Assumes average speed of 30 km/h.
    """

    if distance_km is None:
        return None

    minutes = (distance_km / 30) * 60

    return max(1, round(minutes))


def order_room(order_id):
    return f"order_{order_id}"


def order_to_dict(order):
    return {
        "id": order.id,
        "pickup_address": order.pickup_address,
        "drop_address": order.drop_address,
        "pickup_latitude": order.pickup_latitude,
        "pickup_longitude": order.pickup_longitude,
        "drop_latitude": order.drop_latitude,
        "drop_longitude": order.drop_longitude,
        "rider_id": order.rider_id,
        "status": order.status,
        "rider_latitude": order.rider_latitude,
        "rider_longitude": order.rider_longitude,
        "distance_km": order.distance_km,
        "eta_minutes": order.eta_minutes,
        "created_at": order.created_at.isoformat()
        if order.created_at
        else None,
    }


# =========================
# Basic Routes
# =========================

@app.get("/")
def root():
    return {
        "message": "Real-Time Delivery Tracking API is running"
    }


@app.get("/health")
def health():
    return {
        "status": "ok"
    }


# =========================
# Rider APIs
# =========================

@app.post("/api/riders")
def create_rider(data: RiderCreate):

    db = SessionLocal()

    try:
        rider = Rider(
            name=data.name,
            phone=data.phone,
            latitude=data.latitude,
            longitude=data.longitude,
            available=True,
        )

        db.add(rider)
        db.commit()
        db.refresh(rider)

        return {
            "message": "Rider created successfully",
            "rider": {
                "id": rider.id,
                "name": rider.name,
                "phone": rider.phone,
                "latitude": rider.latitude,
                "longitude": rider.longitude,
                "available": rider.available,
            },
        }

    finally:
        db.close()


@app.get("/api/riders")
def get_riders():

    db = SessionLocal()

    try:
        riders = db.query(Rider).all()

        return [
            {
                "id": rider.id,
                "name": rider.name,
                "phone": rider.phone,
                "latitude": rider.latitude,
                "longitude": rider.longitude,
                "available": rider.available,
            }
            for rider in riders
        ]

    finally:
        db.close()


# =========================
# Order Creation
# =========================

@app.post("/api/orders")
def create_order(data: OrderCreate):

    db = SessionLocal()

    try:
        riders = (
            db.query(Rider)
            .filter(Rider.available == True)
            .all()
        )

        # Auto-seed a rider right near the pickup location if none are
        # available yet, so order creation never blocks on an empty
        # riders table (Fatima's fix — kept as-is, it's a nice touch).
        if not riders:
            demo_rider = Rider(
                name="Express Rider",
                phone="03001234567",
                latitude=data.pickup_latitude,
                longitude=data.pickup_longitude,
                available=True,
            )
            db.add(demo_rider)
            db.commit()
            db.refresh(demo_rider)
            riders = [demo_rider]

        # Find nearest rider to pickup location
        nearest_rider = min(
            riders,
            key=lambda rider: haversine(
                data.pickup_latitude,
                data.pickup_longitude,
                rider.latitude,
                rider.longitude,
            ),
        )

        rider_distance = haversine(
            data.pickup_latitude,
            data.pickup_longitude,
            nearest_rider.latitude,
            nearest_rider.longitude,
        )

        order = Order(
            pickup_address=data.pickup_address,
            drop_address=data.drop_address,

            pickup_latitude=data.pickup_latitude,
            pickup_longitude=data.pickup_longitude,

            drop_latitude=data.drop_latitude,
            drop_longitude=data.drop_longitude,

            rider_id=nearest_rider.id,

            status="Rider Assigned",

            rider_latitude=nearest_rider.latitude,
            rider_longitude=nearest_rider.longitude,

            distance_km=rider_distance,
            eta_minutes=calculate_eta(rider_distance),
        )

        nearest_rider.available = False

        db.add(order)
        db.commit()
        db.refresh(order)

        return {
            "message": "Order created and rider assigned",
            "order": order_to_dict(order),
        }

    finally:
        db.close()


# =========================
# Get Order
# =========================

@app.get("/api/orders/{order_id}")
def get_order(order_id: int):

    db = SessionLocal()

    try:
        order = db.query(Order).filter(Order.id == order_id).first()

        if not order:
            raise HTTPException(
                status_code=404,
                detail="Order not found"
            )

        return order_to_dict(order)

    finally:
        db.close()


# =========================
# REST Rider Location
# (restored — was removed in the version Fatima sent; kept in case the
# frontend still relies on updating location via a plain REST call
# instead of, or in addition to, the Socket.IO event below)
# =========================

@app.post("/api/riders/location")
async def update_rider_location(data: RiderLocationUpdate):

    db = SessionLocal()

    try:
        rider = (
            db.query(Rider)
            .filter(Rider.id == data.rider_id)
            .first()
        )

        order = (
            db.query(Order)
            .filter(Order.id == data.order_id)
            .first()
        )

        if not rider:
            raise HTTPException(
                status_code=404,
                detail="Rider not found"
            )

        if not order:
            raise HTTPException(
                status_code=404,
                detail="Order not found"
            )

        if order.rider_id != rider.id:
            raise HTTPException(
                status_code=403,
                detail="Rider is not assigned to this order"
            )

        rider.latitude = data.latitude
        rider.longitude = data.longitude

        order.rider_latitude = data.latitude
        order.rider_longitude = data.longitude

        distance = haversine(
            data.latitude,
            data.longitude,
            order.drop_latitude,
            order.drop_longitude,
        )

        order.distance_km = distance
        order.eta_minutes = calculate_eta(distance)

        db.commit()

        location_data = {
            "order_id": order.id,
            "rider_id": rider.id,
            "latitude": data.latitude,
            "longitude": data.longitude,
            "distance_km": round(distance, 3),
            "eta_minutes": order.eta_minutes,
        }

        await sio.emit(
            "riderLocationUpdate",
            location_data,
            room=order_room(order.id),
        )

        return {
            "message": "Location updated",
            **location_data,
        }

    finally:
        db.close()


# =========================
# REST Order Status
# (restored — was removed in the version Fatima sent)
# =========================

@app.patch("/api/orders/{order_id}/status")
async def update_order_status(
    order_id: int,
    data: StatusUpdate
):

    if data.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Use one of: {VALID_STATUSES}"
        )

    db = SessionLocal()

    try:
        order = (
            db.query(Order)
            .filter(Order.id == order_id)
            .first()
        )

        if not order:
            raise HTTPException(
                status_code=404,
                detail="Order not found"
            )

        order.status = data.status

        if data.status == "Delivered" and order.rider_id:
            rider = (
                db.query(Rider)
                .filter(Rider.id == order.rider_id)
                .first()
            )

            if rider:
                rider.available = True

        db.commit()

        status_data = {
            "order_id": order.id,
            "status": order.status,
        }

        await sio.emit(
            "orderStatusUpdate",
            status_data,
            room=order_room(order.id),
        )

        return {
            "message": "Order status updated",
            **status_data,
        }

    finally:
        db.close()


# =========================
# Socket.IO Events
# =========================

@sio.event
async def connect(sid, environ, auth):
    print(f"Socket connected: {sid}")


@sio.event
async def disconnect(sid):
    print(f"Socket disconnected: {sid}")


# =========================
# Customer joins order room
# =========================

@sio.event
async def joinOrder(sid, data):

    try:
        order_id = int(data["order_id"])
    except (KeyError, TypeError, ValueError):
        await sio.emit(
            "error",
            {"message": "Valid order_id is required"},
            to=sid,
        )
        return

    db = SessionLocal()

    try:
        order = (
            db.query(Order)
            .filter(Order.id == order_id)
            .first()
        )

        if not order:
            await sio.emit(
                "error",
                {"message": "Order not found"},
                to=sid,
            )
            return

        await sio.enter_room(
            sid,
            order_room(order_id)
        )

        await sio.emit(
            "orderStatusUpdate",
            {
                "order_id": order.id,
                "status": order.status,
            },
            to=sid,
        )

        print(
            f"Socket {sid} joined order room {order_id}"
        )

    finally:
        db.close()


# =========================
# Rider sends live location (via Socket.IO)
# =========================

@sio.event
async def riderLocationUpdate(sid, data):

    db = SessionLocal()

    try:
        rider_id = int(data["rider_id"])
        order_id = int(data["order_id"])
        latitude = float(data["latitude"])
        longitude = float(data["longitude"])

        rider = (
            db.query(Rider)
            .filter(Rider.id == rider_id)
            .first()
        )

        order = (
            db.query(Order)
            .filter(Order.id == order_id)
            .first()
        )

        if not rider:
            await sio.emit(
                "error",
                {"message": "Rider not found"},
                to=sid,
            )
            return

        if not order:
            await sio.emit(
                "error",
                {"message": "Order not found"},
                to=sid,
            )
            return

        if order.rider_id != rider.id:
            await sio.emit(
                "error",
                {"message": "Rider is not assigned to this order"},
                to=sid,
            )
            return

        rider.latitude = latitude
        rider.longitude = longitude

        order.rider_latitude = latitude
        order.rider_longitude = longitude

        distance = haversine(
            latitude,
            longitude,
            order.drop_latitude,
            order.drop_longitude,
        )

        eta = calculate_eta(distance)

        order.distance_km = distance
        order.eta_minutes = eta

        db.commit()

        location_data = {
            "order_id": order.id,
            "rider_id": rider.id,
            "latitude": latitude,
            "longitude": longitude,
            "distance_km": round(distance, 3),
            "eta_minutes": eta,
        }

        await sio.emit(
            "riderLocationUpdate",
            location_data,
            room=order_room(order.id),
        )

        # 100 meter geofence
        if distance <= 0.1 and not order.arriving_soon_notified:

            order.arriving_soon_notified = True
            db.commit()

            await sio.emit(
                "arrivingSoon",
                {
                    "order_id": order.id,
                    "message": "Rider is arriving soon",
                    "distance_km": round(distance, 3),
                },
                room=order_room(order.id),
            )

    except (KeyError, TypeError, ValueError):

        await sio.emit(
            "error",
            {
                "message": (
                    "rider_id, order_id, latitude and "
                    "longitude are required"
                )
            },
            to=sid,
        )

    finally:
        db.close()


# =========================
# Order status update (via Socket.IO)
# (restored — was removed in the version Fatima sent)
# =========================

@sio.event
async def updateOrderStatus(sid, data):

    db = SessionLocal()

    try:
        order_id = int(data["order_id"])
        status = data["status"]

        if status not in VALID_STATUSES:
            await sio.emit(
                "error",
                {
                    "message": f"Invalid status. Use: {VALID_STATUSES}"
                },
                to=sid,
            )
            return

        order = (
            db.query(Order)
            .filter(Order.id == order_id)
            .first()
        )

        if not order:
            await sio.emit(
                "error",
                {"message": "Order not found"},
                to=sid,
            )
            return

        order.status = status

        if status == "Delivered" and order.rider_id:

            rider = (
                db.query(Rider)
                .filter(Rider.id == order.rider_id)
                .first()
            )

            if rider:
                rider.available = True

        db.commit()

        status_data = {
            "order_id": order.id,
            "status": order.status,
        }

        await sio.emit(
            "orderStatusUpdate",
            status_data,
            room=order_room(order.id),
        )

    except (KeyError, TypeError, ValueError):

        await sio.emit(
            "error",
            {
                "message": "order_id and status are required"
            },
            to=sid,
        )

    finally:
        db.close()


# =========================
# Run Server
# =========================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))

    uvicorn.run(
        "main:socket_app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )