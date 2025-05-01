from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi import FastAPI, File, Form, UploadFile, Query, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from urllib.parse import unquote
from bs4 import BeautifulSoup
from typing import List, Optional
from enum import Enum
from sqlmodel import Field, SQLModel, create_engine, Session, select, update, delete
import pymysql
pymysql.install_as_MySQLdb()
from datetime import datetime, timedelta
import uuid
import requests
import logging
import re
import os
import json
import html as html_module

logger = logging.getLogger('uvicorn.error')

class TipoVocacionalEnum(str, Enum):
    uno = "1"
    dos = "2"

class AreaEnum(str, Enum):
    sociales = "1"
    ingenieria = "2"
    biologia = "3"

# --- MODELO SQLMODEL PARA SESIONES ---
class Sesion(SQLModel, table=True):
    id: str = Field(primary_key=True)
    email: str
    cookies: str  # json.dumps de las cookies
    fecha_login: datetime = Field(default_factory=datetime.utcnow)

class PreguntaVocacional(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    denominacion: str
    tipo: TipoVocacionalEnum
    area: AreaEnum
    puntaje: int
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

class RespuestaEstudianteVocacional(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    estudiante_id: int
    estudiante_nombre: str
    estudiante_dni: str

    puntaje_ingeneria: int
    puntaje_biologia: int
    puntaje_sociales: int

    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

class RespuestaEstudianteVocacionalDetalle(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    
    nro_documento: str = Field(max_length=30)
    puntaje: int
    tipo: str = Field(max_length=1, description="0: No, 1: Sí")

    preguntas_id: int = Field(foreign_key="preguntavocacional.id")
    respuesta_id: int = Field(foreign_key="respuestaestudiantevocacional.id")

    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
# --- BASE DE DATOS ---
# engine = create_engine("sqlite:///sesiones.db")
# Usando pymysql como conector
# Nueva conexión a MySQL

DATABASE_URL = "mysql+pymysql://cepreuna_user:C3pr3Un4%402025@127.0.0.1/cepreuna_db"
#DATABASE_URL = "mysql+pymysql://root:@localhost:3306/cepreuna_test_db"
engine = create_engine(DATABASE_URL, echo=True)
SQLModel.metadata.create_all(engine)


SESSION_TIMEOUT_MINUTES = 60  # Tiempo de expiración de sesión

def guardar_sesion(session_id: str, email: str, cookies: dict):
    with Session(engine) as db:
        db.add(Sesion(id=session_id, email=email, cookies=json.dumps(cookies)))
        db.commit()

def obtener_sesion(session_id: str) -> Optional[Sesion]:
    with Session(engine) as db:
        sesion = db.get(Sesion, session_id)
        if not sesion:
            return None
        if datetime.utcnow() - sesion.fecha_login > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
            db.delete(sesion)
            db.commit()
            return None
        return sesion


app = FastAPI()

origins = ["http://localhost:3000",
           "http://127.0.0.1:3000",
           "https://waready.github.io/cepreuna-frontend/",
           "https://waready.github.io"]  # no se usará directamente, pero lo dejamos para completar

app.add_middleware(
    CORSMiddleware,
    #allow_origin_regex='.*',  # ✅ permite cualquier dominio usando regex
    allow_origins=origins, 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#########################
###### Interfaces #######
######################### 
class LoginRequest(BaseModel):
    email: str
    password: str

class TokenRequest(BaseModel):
    tokens: List[str]

class Detalle(BaseModel):
    nro_documento: str
    puntaje: int
    tipo: str
    preguntas_id: int
    respuesta_id: int

class RespuestaConDetalles(BaseModel):
    estudiante_id: int
    estudiante_nombre: str
    estudiante_dni: str
    puntaje_ingeneria: int
    puntaje_biologia: int
    puntaje_sociales: int
    detalles: List[Detalle]

#########################
###### Funciones  #######
######################### 

class CepreunaAPI:

###############################
###### Configuraciones  #######
############################### 

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.base_url = "https://app.cepreuna.edu.pe"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })
        self._load_cookies()

    def _save_cookies(self, email: str):
        cookies = self.session.cookies.get_dict()
        guardar_sesion(self.session_id, email, cookies)

    def _load_cookies(self):
        sesion = obtener_sesion(self.session_id)
        if sesion:
            try:
                cookies = json.loads(sesion.cookies)
                self.session.cookies.update(cookies)
            except Exception as e:
                logger.warning(f"Error al cargar cookies de DB: {e}")

    def _get_decoded_cookie(self, name):
        cookie = self.session.cookies.get(name)
        return unquote(cookie) if cookie else None

    def login(self, email, password):
        self.logout()
        self.session.get(f"{self.base_url}/")
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        if not xsrf_token:
            return False

        response = self.session.post(
            f"{self.base_url}/login-singsuit",
            json={"email": email, "password": password},
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url,
                "Content-Type": "application/json"
            }
        )

        if response.status_code == 200:
            self._save_cookies(email)
            return True
        return False

    def logout(self):
        self.session.cookies.clear()
        self.session.close()
        with Session(engine) as db:
            sesion = db.get(Sesion, self.session_id)
            if sesion:
                db.delete(sesion)
                db.commit()

    def is_logged_in(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        return xsrf_token is not None
        
############################
###### Response Json #######
############################
    def get_validar_pago(self, user_id, pagar_en_pagalo, secuencia, monto, fecha, documento, file):
        logger.warning(pagar_en_pagalo)
        if not pagar_en_pagalo:
            pagar_en_pagalo = ""
        response = self.session.post(
            f"https://sistemas.cepreuna.edu.pe/api/pagos/validar-pago-cuota/{user_id}",
            data={
                "pagarEnPagalo": pagar_en_pagalo,
                "secuencia": secuencia,
                "monto": monto,
                "fecha": fecha,
                "documento": documento
            },
            files={
                "file": (file.filename, file.file, file.content_type)
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json"
            }
        )
        logger.info(response)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 401:
            return {"error": "No autorizado (401). La sesión ha caducado. Vuelva a iniciar sesión."}
        elif response.status_code == 404:
            return {"error": "Recurso no encontrado (404). La ruta puede haber cambiado."}
        else:
            return {"error": f"Error inesperado ({response.status_code}): {response.text}"}

    def registrar_pago_cuota(self, tokens):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        response = self.session.post(
            f"{self.base_url}/estudiantes/registrar-pago-cuota",
            json={"tokens": tokens},
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url,
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
        )
        logger.info(response)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 401:
            return {"error": "No autorizado (401). La sesión ha caducado. Vuelva a iniciar sesión."}
        elif response.status_code == 404:
            return {"error": "Recurso no encontrado (404). La ruta puede haber cambiado."}
        else:
            return {"error": f"Error inesperado ({response.status_code}): {response.text}"}

    def get_horario(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        response = self.session.get(
            f"{self.base_url}/estudiantes/get-horario",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )
        logger.info(response)
        if response.status_code == 200:
            return response.json() 
        elif response.status_code == 401:
            return {"error": "No autorizado (401). La sesión ha caducado. Vuelva a iniciar sesión."}
        elif response.status_code == 404:
            return {"error": "Recurso no encontrado (404). La ruta puede haber cambiado."}
        else:
            return {"error": f"Error inesperado ({response.status_code}): {response.text}"}
    
    def get_carga(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        response = self.session.get(
            f"{self.base_url}/estudiantes/cursos/get-carga",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )
        if response.status_code == 200:
            return response.json() 
        elif response.status_code == 401:
            return {"error": "No autorizado (401). La sesión ha caducado. Vuelva a iniciar sesión."}
        elif response.status_code == 404:
            return {"error": "Recurso no encontrado (404). La ruta puede haber cambiado."}
        else:
            return {"error": f"Error inesperado ({response.status_code}): {response.text}"}

    def get_asistencias(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        response = self.session.get(
            f"{self.base_url}/estudiantes/get-asistencias",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )
        if response.status_code == 200:
            return response.json() 
        elif response.status_code == 401:
            return {"error": "No autorizado (401). La sesión ha caducado. Vuelva a iniciar sesión."}
        elif response.status_code == 404:
            return {"error": "Recurso no encontrado (404). La ruta puede haber cambiado."}
        else:
            return {"error": f"Error inesperado ({response.status_code}): {response.text}"}
    
    def get_rango_fechas(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        response = self.session.get(
            f"{self.base_url}/estudiantes/get-rango-fechas",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )
        if response.status_code == 200:
            return response.json() 
        elif response.status_code == 401:
            return {"error": "No autorizado (401). La sesión ha caducado. Vuelva a iniciar sesión."}
        elif response.status_code == 404:
            return {"error": "Recurso no encontrado (404). La ruta puede haber cambiado."}
        else:
            return {"error": f"Error inesperado ({response.status_code}): {response.text}"}
    
    def get_cuadernillos(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        response = self.session.get(
            f"{self.base_url}/estudiantes/cursos/get-cursos-estudiante",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )
        if response.status_code == 200:
            return response.json() 
        elif response.status_code == 401:
            return {"error": "No autorizado (401). La sesión ha caducado. Vuelva a iniciar sesión."}
        elif response.status_code == 404:
            return {"error": "Recurso no encontrado (404). La ruta puede haber cambiado."}
        else:
            return {"error": f"Error inesperado ({response.status_code}): {response.text}"}

    def get_criterios_docente(self, modalidad=1):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        response = self.session.get(
            f"{self.base_url}/estudiantes/cursos/get-criterios-docente?modalidad={modalidad}",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )
        if response.status_code == 200:
            return response.json() 
        elif response.status_code == 401:
            return {"error": "No autorizado (401). La sesión ha caducado. Vuelva a iniciar sesión."}
        elif response.status_code == 404:
            return {"error": "Recurso no encontrado (404). La ruta puede haber cambiado."}
        else:
            return {"error": f"Error inesperado ({response.status_code}): {response.text}"}

    def get_publicaciones(self, page=1, tipo=1):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        response = self.session.get(
            f"{self.base_url}/get-publicaciones?page={page}&tipo={tipo}",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )

        if response.status_code != 200:
            logger.warning(f"Error {response.status_code} al obtener publicaciones.")
            return None

        try:
            publicaciones_data = response.json()
            publicaciones = publicaciones_data.get("data", [])

            for pub in publicaciones:
                pub_id = pub.get("id")
                user_id = pub.get("user_id")
                rol_name = pub.get("rol", {}).get("name")

                # Verificamos que todos los datos estén presentes
                if pub_id and user_id and rol_name:
                    try:
                        data_response = self.session.get(
                            f"{self.base_url}/recursos/get-data-user",
                            params={
                                "id": pub_id,
                                "idUser": user_id,
                                "rolName": rol_name
                            },
                            headers={
                                "X-XSRF-TOKEN": xsrf_token,
                                "Referer": self.base_url
                            }
                        )

                        if data_response.status_code == 200:
                            extra_data = data_response.json()
                            pub["datos_usuario"] = extra_data.get("datos", {})
                        else:
                            logger.warning(f"No se pudo obtener datos del usuario para publicación {pub_id}")
                    except Exception as e:
                        logger.error(f"Error al obtener datos del usuario para publicación {pub_id}: {e}")
                else:
                    logger.warning(f"Publicación sin datos completos: ID: {pub_id}, USER_ID: {user_id}, ROL: {rol_name}")

            return publicaciones_data

        except Exception as e:
            logger.error(f"No se pudo parsear JSON de publicaciones: {e}")
            return None


    def get_cuadernillos_format(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        response = self.session.get(
            f"{self.base_url}/estudiantes/cursos/get-cursos-estudiante",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": f"{self.base_url}/estudiantes/cursos/cuadernillos"
            }
        )
        if response.status_code == 200:
            try:
                data = response.json()
                processed_data = []
                for curso in data.get('cuadernillos', []):
                    if curso.get('cuadernillos'):
                        for cuadernillo in curso['cuadernillos']:
                            processed_data.append({
                                'curso': curso['denominacion'],
                                'semana': cuadernillo['semana'],
                                'url': f"{curso['base_path']}/{cuadernillo['path']}",
                                'color': curso['color']
                            })
                return {'cuadernillos': processed_data}
            except ValueError:
                return {"cuadernillos": []}
        return {"cuadernillos": []}
   
    def crear_publicacion(self, usuario: dict, texto: str, tipo: int, imagen: Optional[UploadFile] = None):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")

        headers = {
            "X-XSRF-TOKEN": xsrf_token,
            "Referer": self.base_url
        }

        # Form data
        data = {
            "usuario": json.dumps(usuario),
            "texto": texto,
            "tipo": str(tipo)
        }

        files = {}
        if imagen:
            files["imagen"] = (imagen.filename, imagen.file, imagen.content_type)

        try:
            response = self.session.post(
                f"{self.base_url}/crear-publicacion",
                headers=headers,
                data=data,
                files=files if files else None
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Error {response.status_code} al crear publicación.")
                return None

        except Exception as e:
            logger.error(f"Error al enviar publicación: {e}")
            return None


#################################
###### Pantallas Inertia  #######
#################################

    def get_page_dashboard(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        html_response = self.session.get(
            f"{self.base_url}/dashboard",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )

        if html_response.status_code != 200:
            logger.warning(f"Fallo al obtener /dashboard (código {html_response.status_code})")
            return None

        soup = BeautifulSoup(html_response.text, "html.parser")
        div_app = soup.find("div", id="app")
        data_page_raw = div_app.get("data-page") if div_app else None

        if not data_page_raw:
            logger.warning("No se encontró data-page en el HTML.")
            return None

        try:
            data_page_json = html_module.unescape(data_page_raw)
            page_data = json.loads(data_page_json)
            inertia_version = page_data.get("version")
            if not inertia_version:
                logger.warning("No se encontró la versión Inertia.")
                return None
        except Exception as e:
            logger.error(f"Error al parsear JSON desde data-page: {e}")
            return None

        inertia_response = self.session.get(
            f"{self.base_url}/dashboard",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url,
                "X-Inertia": "true",
                "X-Inertia-Version": inertia_version,
                "Accept": "application/json"
            }
        )

        if inertia_response.status_code == 200:
            try:
                return inertia_response.json()
            except Exception as e:
                logger.error(f"No se pudo parsear JSON final: {e}")
                return None

        logger.warning(f"Fallo al obtener Inertia JSON (código {inertia_response.status_code})")
        return None

    def get_page_perfil(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        # 1. Obtener HTML sin headers de Inertia
        html_response = self.session.get(
            f"{self.base_url}/perfil",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )

        if html_response.status_code != 200:
            logger.warning(f"Fallo al obtener /perfil (código {html_response.status_code})")
            return None

        # 2. Guardar el HTML para depuración (opcional)
        with open("perfil_raw.html", "w", encoding="utf-8") as f:
            f.write(html_response.text)

        # 3. Procesar el HTML con BeautifulSoup
        soup = BeautifulSoup(html_response.text, "html.parser")
        div_app = soup.find("div", id="app")
        data_page_raw = div_app.get("data-page") if div_app else None

        if not data_page_raw:
            logger.warning("No se encontró data-page en el HTML.")
            return None

        try:
            data_page_json = html_module.unescape(data_page_raw)
            page_data = json.loads(data_page_json)
            inertia_version = page_data.get("version")
            if not inertia_version:
                logger.warning("No se encontró la versión Inertia.")
                return None
        except Exception as e:
            logger.error(f"Error al parsear JSON desde data-page: {e}")
            return None

        # 4. Segunda petición con headers Inertia válidos
        inertia_response = self.session.get(
            f"{self.base_url}/perfil",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url,
                "X-Inertia": "true",
                "X-Inertia-Version": inertia_version,
                "Accept": "application/json"
            }
        )

        if inertia_response.status_code == 200:
            try:
                return inertia_response.json()
            except Exception as e:
                logger.error(f"No se pudo parsear JSON final: {e}")
                return None

        logger.warning(f"Fallo al obtener Inertia JSON (código {inertia_response.status_code})")
        return None

    def get_page_horarios(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        # 1. Obtener HTML sin headers de Inertia
        html_response = self.session.get(
            f"{self.base_url}/estudiantes/horarios",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )

        if html_response.status_code != 200:
            logger.warning(f"Fallo al obtener /estudiantes/horarios (código {html_response.status_code})")
            return None

        # 2. Guardar el HTML para depuración (opcional)
        with open("horarios_raw.html", "w", encoding="utf-8") as f:
            f.write(html_response.text)

        # 3. Procesar el HTML con BeautifulSoup
        soup = BeautifulSoup(html_response.text, "html.parser")
        div_app = soup.find("div", id="app")
        data_page_raw = div_app.get("data-page") if div_app else None

        if not data_page_raw:
            logger.warning("No se encontró data-page en el HTML.")
            return None

        try:
            data_page_json = html_module.unescape(data_page_raw)
            page_data = json.loads(data_page_json)
            inertia_version = page_data.get("version")
            if not inertia_version:
                logger.warning("No se encontró la versión Inertia.")
                return None
        except Exception as e:
            logger.error(f"Error al parsear JSON desde data-page: {e}")
            return None

        # 4. Segunda petición con headers Inertia válidos
        inertia_response = self.session.get(
            f"{self.base_url}/estudiantes/horarios",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url,
                "X-Inertia": "true",
                "X-Inertia-Version": inertia_version,
                "Accept": "application/json"
            }
        )

        if inertia_response.status_code == 200:
            try:
                return inertia_response.json()
            except Exception as e:
                logger.error(f"No se pudo parsear JSON final: {e}")
                return None

        logger.warning(f"Fallo al obtener Inertia JSON (código {inertia_response.status_code})")
        return None

    def get_page_mis_cursos(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        # 1. Obtener HTML sin headers de Inertia
        html_response = self.session.get(
            f"{self.base_url}/estudiantes/cursos/mis-cursos",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )

        if html_response.status_code != 200:
            logger.warning(f"Fallo al obtener /estudiantes/cursos/mis-cursos (código {html_response.status_code})")
            return None

        # 2. Guardar el HTML para depuración (opcional)
        with open("mis_cursos.html", "w", encoding="utf-8") as f:
            f.write(html_response.text)

        # 3. Procesar el HTML con BeautifulSoup
        soup = BeautifulSoup(html_response.text, "html.parser")
        div_app = soup.find("div", id="app")
        data_page_raw = div_app.get("data-page") if div_app else None

        if not data_page_raw:
            logger.warning("No se encontró data-page en el HTML.")
            return None

        try:
            data_page_json = html_module.unescape(data_page_raw)
            page_data = json.loads(data_page_json)
            inertia_version = page_data.get("version")
            if not inertia_version:
                logger.warning("No se encontró la versión Inertia.")
                return None
        except Exception as e:
            logger.error(f"Error al parsear JSON desde data-page: {e}")
            return None

        # 4. Segunda petición con headers Inertia válidos
        inertia_response = self.session.get(
            f"{self.base_url}/estudiantes/cursos/mis-cursos",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url,
                "X-Inertia": "true",
                "X-Inertia-Version": inertia_version,
                "Accept": "application/json"
            }
        )

        if inertia_response.status_code == 200:
            try:
                return inertia_response.json()
            except Exception as e:
                logger.error(f"No se pudo parsear JSON final: {e}")
                return None

        logger.warning(f"Fallo al obtener Inertia JSON (código {inertia_response.status_code})")
        return None

    def get_page_cuadernillo(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        # 1. Obtener HTML sin headers de Inertia
        html_response = self.session.get(
            f"{self.base_url}/estudiantes/cursos/cuadernillos",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )

        if html_response.status_code != 200:
            logger.warning(f"Fallo al obtener /estudiantes/cursos/cuadernillos (código {html_response.status_code})")
            return None

        # 2. Guardar el HTML para depuración (opcional)
        with open("cuadernillos.html", "w", encoding="utf-8") as f:
            f.write(html_response.text)

        # 3. Procesar el HTML con BeautifulSoup
        soup = BeautifulSoup(html_response.text, "html.parser")
        div_app = soup.find("div", id="app")
        data_page_raw = div_app.get("data-page") if div_app else None

        if not data_page_raw:
            logger.warning("No se encontró data-page en el HTML.")
            return None

        try:
            data_page_json = html_module.unescape(data_page_raw)
            page_data = json.loads(data_page_json)
            inertia_version = page_data.get("version")
            if not inertia_version:
                logger.warning("No se encontró la versión Inertia.")
                return None
        except Exception as e:
            logger.error(f"Error al parsear JSON desde data-page: {e}")
            return None

        # 4. Segunda petición con headers Inertia válidos
        inertia_response = self.session.get(
            f"{self.base_url}/estudiantes/cursos/cuadernillos",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url,
                "X-Inertia": "true",
                "X-Inertia-Version": inertia_version,
                "Accept": "application/json"
            }
        )

        if inertia_response.status_code == 200:
            try:
                return inertia_response.json()
            except Exception as e:
                logger.error(f"No se pudo parsear JSON final: {e}")
                return None

        logger.warning(f"Fallo al obtener Inertia JSON (código {inertia_response.status_code})")
        return None

    def get_page_asistencias(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        # 1. Obtener HTML sin headers de Inertia
        html_response = self.session.get(
            f"{self.base_url}/estudiantes/asistencias",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )

        if html_response.status_code != 200:
            logger.warning(f"Fallo al obtener /estudiantes/asistencias (código {html_response.status_code})")
            return None

        # 2. Guardar el HTML para depuración (opcional)
        with open("asistencias.html", "w", encoding="utf-8") as f:
            f.write(html_response.text)

        # 3. Procesar el HTML con BeautifulSoup
        soup = BeautifulSoup(html_response.text, "html.parser")
        div_app = soup.find("div", id="app")
        data_page_raw = div_app.get("data-page") if div_app else None

        if not data_page_raw:
            logger.warning("No se encontró data-page en el HTML.")
            return None

        try:
            data_page_json = html_module.unescape(data_page_raw)
            page_data = json.loads(data_page_json)
            inertia_version = page_data.get("version")
            if not inertia_version:
                logger.warning("No se encontró la versión Inertia.")
                return None
        except Exception as e:
            logger.error(f"Error al parsear JSON desde data-page: {e}")
            return None

        # 4. Segunda petición con headers Inertia válidos
        inertia_response = self.session.get(
            f"{self.base_url}/estudiantes/asistencias",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url,
                "X-Inertia": "true",
                "X-Inertia-Version": inertia_version,
                "Accept": "application/json"
            }
        )

        if inertia_response.status_code == 200:
            try:
                return inertia_response.json()
            except Exception as e:
                logger.error(f"No se pudo parsear JSON final: {e}")
                return None

        logger.warning(f"Fallo al obtener Inertia JSON (código {inertia_response.status_code})")
        return None

    def get_page_pagos(self):
        xsrf_token = self._get_decoded_cookie("XSRF-TOKEN")
        # 1. Obtener HTML sin headers de Inertia
        html_response = self.session.get(
            f"{self.base_url}/estudiantes/pagos",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url
            }
        )

        if html_response.status_code != 200:
            logger.warning(f"Fallo al obtener /estudiantes/pagos (código {html_response.status_code})")
            return None

        # 2. Guardar el HTML para depuración (opcional)
        with open("pagos.html", "w", encoding="utf-8") as f:
            f.write(html_response.text)

        # 3. Procesar el HTML con BeautifulSoup
        soup = BeautifulSoup(html_response.text, "html.parser")
        div_app = soup.find("div", id="app")
        data_page_raw = div_app.get("data-page") if div_app else None

        if not data_page_raw:
            logger.warning("No se encontró data-page en el HTML.")
            return None

        try:
            data_page_json = html_module.unescape(data_page_raw)
            page_data = json.loads(data_page_json)
            inertia_version = page_data.get("version")
            if not inertia_version:
                logger.warning("No se encontró la versión Inertia.")
                return None
        except Exception as e:
            logger.error(f"Error al parsear JSON desde data-page: {e}")
            return None

        # 4. Segunda petición con headers Inertia válidos
        inertia_response = self.session.get(
            f"{self.base_url}/estudiantes/pagos",
            headers={
                "X-XSRF-TOKEN": xsrf_token,
                "Referer": self.base_url,
                "X-Inertia": "true",
                "X-Inertia-Version": inertia_version,
                "Accept": "application/json"
            }
        )

        if inertia_response.status_code == 200:
            try:
                return inertia_response.json()
            except Exception as e:
                logger.error(f"No se pudo parsear JSON final: {e}")
                return None

        logger.warning(f"Fallo al obtener Inertia JSON (código {inertia_response.status_code})")
        return None

#########################
#######   Rutas  ########
######################### 

# @app.post("/api/login")
# async def handle_login(data: LoginRequest ):
#     session_id = str(uuid.uuid4())
#     api = CepreunaAPI(session_id=session_id)
#     if api.login(data.email, data.password):
#         horario = api.get_criterios_docente(modalidad=1)
#         cuadernillos = api.get_page_cuadernillo()
#         #get_perfil =  api.get_page_horarios()
#         #publicaciones = api.get_publicaciones(page=1, tipo=1)
#         #if horario:
#         return JSONResponse(content={
#             "success": True,
#             "cuadernillo": cuadernillos,
#            # "dashboard":dashboard,
#            # "get_perfil": get_perfil,
#            # "horario": horario,
#            # "cuadernillos": cuadernillos.get('cuadernillos', []),
#             "message": "Datos obtenidos correctamente"
#         })

#     return JSONResponse(content={
#         "success": False,
#         "error": "Credenciales incorrectas o error al obtener datos"
#     })

@app.post("/api/login")
async def handle_login(data: LoginRequest):
    session_id = str(uuid.uuid4())
    api = CepreunaAPI(session_id=session_id)

    if api.login(data.email, data.password):
        cuadernillos = api.get_page_cuadernillo()

        response = JSONResponse(content={
            "success": True,
            "cuadernillo": cuadernillos,
            "message": "Datos obtenidos correctamente"
        })
        response.set_cookie(
            "session_id",
            session_id,
            httponly=True,
            max_age=3600,
            samesite="none",  # si usas dominios cruzados, si no puedes dejarlo en "lax"
            secure=True       # obligatorio en HTTPS
        )
        return response

    return JSONResponse(
        content={"success": False, "error": "Credenciales incorrectas o error al obtener datos"},
        status_code=401
    )

@app.post("/api/logout")
async def handle_logout(session_id: str = Cookie(None)):
    if session_id:
        CepreunaAPI(session_id).logout()
    response = JSONResponse(content={"success": True, "message": "Sesión cerrada correctamente"})
    response.delete_cookie("session_id")
    return response

@app.get("/api/verify-session")
async def verify_session(session_id: str = Cookie(None)):
    if session_id and obtener_sesion(session_id):
        return {"success": True}
    return {"success": False}

#######################################################
@app.get("/api/horario")
async def get_horario(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})

    api = CepreunaAPI(session_id)
    return api.get_horario()

@app.get("/api/carga")
async def get_carga(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_carga()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/asistencias")
async def get_asistencias(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_asistencias()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/rango-fechas")
async def get_rango_fechas(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_rango_fechas()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/cuadernillos")
async def get_cuadernillos(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_cuadernillos()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/cuadernillos-format")
async def get_cuadernillos_format(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_cuadernillos_format()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/criterios-docente")
async def get_criterios_docente(modalidad: int = 1, session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_criterios_docente(modalidad=modalidad)
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/publicaciones")
async def get_publicaciones(page: int = 1, tipo: int = 1, session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_publicaciones(page=page, tipo=tipo)
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.post("/api/pagos/{user_id}")
async def validar_cuota(
    user_id: int,
    pagarEnPagalo: bool = Form(...),
    secuencia: str = Form(...),
    monto: float = Form(...),
    fecha: str = Form(...),
    documento: str = Form(...),
    file: UploadFile = File(...)
):
    api = CepreunaAPI()
    return api.get_validar_pago(
        user_id, pagarEnPagalo, secuencia, monto, fecha, documento, file
    )

@app.post("/api/registrar-pago")
async def registrar_pago(data: TokenRequest, session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.registrar_pago_cuota(tokens=data.tokens)
    
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.post("/api/crear-publicacion")
async def crear_publicacion(
    usuario: str = Form(...),
    texto: str = Form(...),
    tipo: int = Form(...),
    imagen: Optional[UploadFile] = File(None),
    session_id: str = Cookie(None)
):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    
    api = CepreunaAPI(session_id)
    if not api.is_logged_in():
        return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})
    
    return api.crear_publicacion(
        usuario=json.loads(usuario),  # porque viene como string desde FormData
        texto=texto,
        tipo=tipo,
        imagen=imagen
    )


#############################################################

@app.get("/api/page/dashboard")
async def get_dashboard(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    return api.get_page_dashboard()

@app.get("/api/page/perfil")
async def get_page_perfil(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_page_perfil()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/page/horarios")
async def get_page_horarios(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_page_horarios()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/page/mis-cursos")
async def get_page_mis_cursos(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_page_mis_cursos()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/page/cuadernillos")
async def get_page_cuadernillo(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_page_cuadernillo()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/page/asistencias")
async def get_page_asistencias(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_page_asistencias()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/page/pagos")
async def get_page_pagos(session_id: str = Cookie(None)):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id)
    if api.is_logged_in():
        return api.get_page_pagos()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

###################################################################################################
@app.get("/api/preguntas")
def listar_preguntas():
    with Session(engine) as db:
        preguntas = db.exec(select(PreguntaVocacional)).all()
        return preguntas

@app.post("/api/preguntas")
def crear_pregunta(
    denominacion: str = Form(...),
    tipo: str = Form(...),
    area: str = Form(...),
    puntaje: int = Form(...),
    session_id: Optional[str] = Cookie(None)
):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión inválida"})

    pregunta = PreguntaVocacional(
        denominacion=denominacion,
        tipo=tipo,
        area=area,
        puntaje=puntaje
    )
    with Session(engine) as db:
        db.add(pregunta)
        db.commit()
        db.refresh(pregunta)
        return pregunta

@app.get("/api/respuestas")
def listar_respuestas():
    with Session(engine) as db:
        respuestas = db.exec(select(RespuestaEstudianteVocacional)).all()
        return respuestas

@app.post("/api/respuestas")
def crear_respuesta(
    estudiante_id: int = Form(...),
    estudiante_nombre: str = Form(...),
    estudiante_dni: str = Form(...),
    puntaje_ingeneria: int = Form(...),
    puntaje_biologia: int = Form(...),
    puntaje_sociales: int = Form(...),
    session_id: Optional[str] = Cookie(None)
):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión inválida"})

    respuesta = RespuestaEstudianteVocacional(
        estudiante_id=estudiante_id,
        estudiante_nombre=estudiante_nombre,
        estudiante_dni=estudiante_dni,
        puntaje_ingeneria=puntaje_ingeneria,
        puntaje_biologia=puntaje_biologia,
        puntaje_sociales=puntaje_sociales
    )
    with Session(engine) as db:
        db.add(respuesta)
        db.commit()
        db.refresh(respuesta)
        return respuesta

@app.get("/api/respuestas-detalle")
def listar_respuestas_detalle():
    with Session(engine) as db:
        detalles = db.exec(select(RespuestaEstudianteVocacionalDetalle)).all()
        return detalles

@app.post("/api/respuestas-detalle")
def crear_detalle(
    nro_documento: str = Form(...),
    puntaje: int = Form(...),
    tipo: str = Form(...),
    preguntas_id: int = Form(...),
    respuesta_id: int = Form(...),
    session_id: Optional[str] = Cookie(None)
):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión inválida"})

    detalle = RespuestaEstudianteVocacionalDetalle(
        nro_documento=nro_documento,
        puntaje=puntaje,
        tipo=tipo,
        preguntas_id=preguntas_id,
        respuesta_id=respuesta_id
    )
    with Session(engine) as db:
        db.add(detalle)
        db.commit()
        db.refresh(detalle)
        return detalle

@app.post("/api/respuestasAll")
def crear_respuesta(
    datos: RespuestaConDetalles,
    session_id: Optional[str] = Cookie(None)
):
    if not session_id or not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión inválida"})

    with Session(engine) as db:
        respuesta = RespuestaEstudianteVocacional(
            estudiante_id=datos.estudiante_id,
            estudiante_nombre=datos.estudiante_nombre,
            estudiante_dni=datos.estudiante_dni,
            puntaje_ingeneria=datos.puntaje_ingeneria,
            puntaje_biologia=datos.puntaje_biologia,
            puntaje_sociales=datos.puntaje_sociales
        )
        db.add(respuesta)
        db.commit()
        db.refresh(respuesta)

        for d in datos.detalles:
            detalle = RespuestaEstudianteVocacionalDetalle(
                nro_documento=d.nro_documento,
                puntaje=d.puntaje,
                tipo=d.tipo,
                preguntas_id=d.preguntas_id,
                respuesta_id=respuesta.id
            )
            db.add(detalle)

        db.commit()
        return {"mensaje": "Guardado correctamente", "respuesta_id": respuesta.id}