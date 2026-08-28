from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import psycopg2
from pydantic import BaseModel
from typing import List
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
)

DB_CONFIG = {
    "host": "localhost",
    "database": "sistema_kioscos",
    "user": "postgres",
    "password": "facubasedatos" 
}

@app.get("/")
def mostrar_interfaz():
    return FileResponse("index.html")

class NuevoProducto(BaseModel):
    nombre: str
    precio: float
    stock: int
    fecha_vencimiento: str = None  

class ProductoVenta(BaseModel):
    id: int
    cantidad: int
    precio: float

class Venta(BaseModel):
    productos: List[ProductoVenta]
    metodo_pago: str
    usuario_id: int 

class LoginRequest(BaseModel):
    pin: str 

class CierreRequest(BaseModel):
    usuario_id: int 
    efectivo_real: float

class NuevoUsuario(BaseModel):
    nombre: str
    pin: str
    rol: str

@app.get("/usuarios")
def listar_usuarios():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    # 🔥 Agregamos WHERE activo = TRUE
    cur.execute("SELECT id, nombre, rol FROM usuarios WHERE activo = TRUE ORDER BY nombre")
    users = [{"id": u[0], "nombre": u[1], "rol": u[2]} for u in cur.fetchall()]
    cur.close()
    conn.close()
    return users

# 🔥 ACÁ ESTÁ EL CAMBIO: Ahora Python manda la fecha junto con el stock
@app.get("/productos")
def listar_productos():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    # 🔥 Agregamos WHERE activo = TRUE
    cur.execute("SELECT id, nombre, precio_venta, stock_minimo, fecha_vencimiento FROM productos WHERE activo = TRUE ORDER BY nombre ASC")
    prods = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": p[0], "nombre": p[1], "precio": float(p[2]), "stock": p[3], "fecha_vencimiento": p[4].isoformat() if p[4] else None} for p in prods]

@app.get("/reporte-caja")
def reporte_caja():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT SUM(total) FROM ventas WHERE cierre_id IS NULL")
    total = cur.fetchone()[0]
    cur.close()
    conn.close()
    return {"total_recaudado": float(total) if total else 0}

@app.get("/historial-cierres")
def historial_cierres():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.fecha_cierre, c.monto_final, c.ventas_totales, 
               COALESCE(u.nombre, 'Desconocido'), c.diferencia
        FROM cierres_caja c
        LEFT JOIN usuarios u ON c.usuario_id = u.id
        ORDER BY c.fecha_cierre DESC
    """)
    cierres = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {"id": c[0], "fecha": c[1].strftime("%Y-%m-%d %H:%M"), "monto": float(c[2]), 
         "ventas": c[3], "usuario": c[4], "diferencia": float(c[5]) if c[5] is not None else 0} 
        for c in cierres
    ]

@app.get("/alertas-vencimiento")
def alertas_vencimiento():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    # 🔥 Agregamos AND activo = TRUE
    cur.execute("""
        SELECT nombre, fecha_vencimiento 
        FROM productos 
        WHERE fecha_vencimiento IS NOT NULL 
        AND stock_minimo > 0
        AND activo = TRUE
        AND fecha_vencimiento <= CURRENT_DATE + INTERVAL '10 days'
        ORDER BY fecha_vencimiento ASC
    """)
    alertas = cur.fetchall()
    cur.close()
    conn.close()
    return [{"nombre": a[0], "fecha": a[1].strftime("%d/%m/%Y")} for a in alertas]

@app.post("/usuarios")
def crear_usuario(u: NuevoUsuario):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        # 🔥 Chequeamos que el PIN no lo tenga alguien ACTIVO
        cur.execute("SELECT id FROM usuarios WHERE pin = %s AND activo = TRUE", (u.pin,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="Ese PIN ya lo está usando otra persona")
        cur.execute("INSERT INTO usuarios (nombre, pin, rol) VALUES (%s, %s, %s)", (u.nombre, u.pin, u.rol))
        conn.commit()
        return {"status": "Empleado agregado con éxito"}
    except HTTPException as he:
        conn.rollback()
        raise he
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.post("/login")
def login(req: LoginRequest):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        # 🔥 Agregamos AND activo = TRUE para que no entren los despedidos
        cur.execute("SELECT id, nombre, rol FROM usuarios WHERE pin = %s AND activo = TRUE", (req.pin,))
        user = cur.fetchone()
        if user:
            return {"id": user[0], "nombre": user[1], "rol": user[2]}
        else:
            raise HTTPException(status_code=401, detail="PIN Incorrecto o Empleado Inactivo")
    finally:
        cur.close()
        conn.close()

@app.post("/productos")
def crear_o_actualizar_producto(p: NuevoProducto):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        fecha_venc = p.fecha_vencimiento if p.fecha_vencimiento else None
        cur.execute("SELECT id, precio_venta FROM productos WHERE nombre = %s", (p.nombre,))
        existente = cur.fetchone()
        
        if existente:
            precio_final = p.precio if p.precio > 0 else existente[1]
            cur.execute(
                "UPDATE productos SET stock_minimo = stock_minimo + %s, precio_venta = %s, fecha_vencimiento = %s WHERE id = %s", 
                (p.stock, precio_final, fecha_venc, existente[0])
            )
            msg = f"Stock de {p.nombre} actualizado (+{p.stock})"
        else:
            if p.precio <= 0:
                raise HTTPException(status_code=400, detail="Falta el precio")
            cur.execute(
                "INSERT INTO productos (nombre, precio_venta, stock_minimo, negocio_id, fecha_vencimiento) VALUES (%s, %s, %s, 1, %s)",
                (p.nombre, p.precio, p.stock, fecha_venc)
            )
            msg = "Producto creado con éxito"
        
        conn.commit()
        return {"status": msg}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.delete("/productos/{id}")
def eliminar_producto(id: int):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        # 🔥 En vez de DELETE, hacemos un UPDATE para "apagar" el producto
        cur.execute("UPDATE productos SET activo = FALSE WHERE id = %s", (id,))
        conn.commit()
        return {"status": "Producto eliminado (oculto) exitosamente"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.post("/procesar-venta")
def procesar_venta(v: Venta):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        total_venta = sum(item.precio * item.cantidad for item in v.productos)
        cur.execute("INSERT INTO ventas (metodo_pago, total, usuario_id) VALUES (%s, %s, %s) RETURNING id", 
                    (v.metodo_pago, total_venta, v.usuario_id))
        venta_id = cur.fetchone()[0]

        for p in v.productos:
            cur.execute("UPDATE productos SET stock_minimo = stock_minimo - %s WHERE id = %s", (p.cantidad, p.id))
            cur.execute(
                "INSERT INTO ventas_detalle (venta_id, producto_id, cantidad, precio_unitario, subtotal) VALUES (%s, %s, %s, %s, %s)",
                (venta_id, p.id, p.cantidad, p.precio, p.cantidad * p.precio)
            )
        conn.commit()
        return {"status": "Venta exitosa"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.post("/cerrar-caja")
def cerrar_caja(req: CierreRequest):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute("SELECT SUM(total), COUNT(*) FROM ventas WHERE cierre_id IS NULL")
        res = cur.fetchone()
        total_general = float(res[0]) if res[0] else 0
        cant_ventas = res[1] if res[1] else 0

        if cant_ventas == 0:
            raise HTTPException(status_code=400, detail="No hay ventas nuevas para cerrar")

        cur.execute("SELECT SUM(total) FROM ventas WHERE metodo_pago = 'EFECTIVO' AND cierre_id IS NULL")
        res_efectivo = cur.fetchone()
        efectivo_esperado = float(res_efectivo[0]) if res_efectivo[0] else 0
        
        diferencia = req.efectivo_real - efectivo_esperado

        cur.execute(
            """INSERT INTO cierres_caja 
               (monto_final, ventas_totales, usuario_id, efectivo_esperado, efectivo_real, diferencia) 
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (total_general, cant_ventas, req.usuario_id, efectivo_esperado, req.efectivo_real, diferencia)
        )
        nuevo_cierre_id = cur.fetchone()[0]
        
        cur.execute("UPDATE ventas SET cierre_id = %s WHERE cierre_id IS NULL", (nuevo_cierre_id,))
        
        conn.commit()
        return {
            "id": nuevo_cierre_id, "monto": total_general, "ventas": cant_ventas,
            "esperado": efectivo_esperado, "real": req.efectivo_real, "diferencia": diferencia
        }
    except HTTPException as he:
        conn.rollback()
        raise he
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.delete("/usuarios/{id}")
def eliminar_usuario(id: int):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute("SELECT rol FROM usuarios WHERE id = %s", (id,))
        user = cur.fetchone()
        if user and user[0] == 'admin':
            raise HTTPException(status_code=400, detail="No se puede eliminar al Administrador")
        
        # 🔥 En vez de DELETE, hacemos un UPDATE
        cur.execute("UPDATE usuarios SET activo = FALSE WHERE id = %s", (id,))
        conn.commit()
        return {"status": "Empleado eliminado"}
    except HTTPException as he:
        conn.rollback()
        raise he
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()

@app.get("/cierre-detalles/{cierre_id}")
def obtener_detalles_cierre(cierre_id: int):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        SELECT v.id, v.fecha, v.total, v.metodo_pago,
               string_agg(p.nombre || ' (x' || vd.cantidad || ')', ', ')
        FROM ventas v
        JOIN ventas_detalle vd ON v.id = vd.venta_id
        JOIN productos p ON vd.producto_id = p.id
        WHERE v.cierre_id = %s
        GROUP BY v.id, v.fecha, v.total, v.metodo_pago
    """, (cierre_id,))
    ventas = cur.fetchall()
    cur.close()
    conn.close()
    return [{"id": v[0], "fecha": v[1].strftime("%H:%M"), "total": float(v[2]), "pago": v[3], "items": v[4]} for v in ventas]