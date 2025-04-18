from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi import FastAPI, File, Form, UploadFile, Query
from pydantic import BaseModel
from urllib.parse import unquote
from bs4 import BeautifulSoup
from typing import List, Optional
from sqlmodel import Field, SQLModel, create_engine, Session
from datetime import datetime, timedelta
import uuid
import requests
import logging
import re
import os
import json
import html as html_module

logger = logging.getLogger('uvicorn.error')

# --- MODELO SQLMODEL PARA SESIONES ---
class Sesion(SQLModel, table=True):
    id: str = Field(primary_key=True)
    email: str
    cookies: str  # json.dumps de las cookies
    fecha_login: datetime = Field(default_factory=datetime.utcnow)

# --- BASE DE DATOS ---
engine = create_engine("sqlite:///sesiones.db")
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
#########################
###### Interfaces #######
######################### 
class LoginRequest(BaseModel):
    email: str
    password: str

class TokenRequest(BaseModel):
    tokens: List[str]
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
        if response.status_code == 200:
            try:
                return response.json()
            except Exception as e:
                logger.error(f"No se pudo parsear JSON de publicaciones: {e}")
                return None
        else:
            logger.warning(f"Error {response.status_code} al obtener publicaciones.")
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

@app.post("/api/login")
async def handle_login(data: LoginRequest ):
    session_id = str(uuid.uuid4())
    api = CepreunaAPI(session_id=session_id)
    if api.login(data.email, data.password):
        horario = api.get_criterios_docente(modalidad=1)
        cuadernillos = api.get_page_cuadernillo()
        #get_perfil =  api.get_page_horarios()
        #publicaciones = api.get_publicaciones(page=1, tipo=1)
        #if horario:
        return JSONResponse(content={
            "success": True,
            "cuadernillo": cuadernillos,
           # "dashboard":dashboard,
           # "get_perfil": get_perfil,
           # "horario": horario,
           # "cuadernillos": cuadernillos.get('cuadernillos', []),
            "message": "Datos obtenidos correctamente"
        })

    return JSONResponse(content={
        "success": False,
        "error": "Credenciales incorrectas o error al obtener datos"
    })

@app.post("/api/logout")
async def handle_logout():
    api = CepreunaAPI()
    api.logout()
    return JSONResponse(content={
        "success": True,
        "message": "Sesión cerrada correctamente"
    })
#######################################################
@app.get("/api/horario")
async def get_horario(session_id: str = Query(...)):
    if not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id=session_id)
    return api.get_horario()

@app.get("/api/carga")
async def get_carga():
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_carga()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/asistencias")
async def get_asistencias():
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_asistencias()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/rango-fechas")
async def get_rango_fechas():
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_rango_fechas()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/cuadernillos")
async def get_cuadernillos():
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_cuadernillos()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})
        

@app.get("/api/cuadernillos-format")
async def get_cuadernillos_format():
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_cuadernillos_format()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/criterios-docente")
async def get_criterios_docente(modalidad: int = 1):
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_criterios_docente(modalidad=modalidad)
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/publicaciones")
async def get_publicaciones(page: int = 1, tipo: int = 1):
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_publicaciones(page=page, tipo=tipo)
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
async def registrar_pago(data: TokenRequest):
    api = CepreunaAPI()
    if api.is_logged_in():
        return api.registrar_pago_cuota(tokens=data.tokens)
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

#############################################################

@app.get("/api/page/dashboard")
async def get_dashboard(session_id: str = Query(...)):
    if not obtener_sesion(session_id):
        return JSONResponse(status_code=403, content={"error": "Sesión no válida o expirada."})
    api = CepreunaAPI(session_id=session_id)
    return api.get_page_dashboard()

@app.get("/api/page/perfil")
async def get_page_perfil():
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_page_perfil()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/page/horarios")
async def get_page_horarios():
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_page_horarios()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/page/mis-cursos")
async def get_page_mis_cursos():
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_page_mis_cursos()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/page/cuadernillos")
async def get_page_cuadernillo():
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_page_cuadernillo()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/page/asistencias")
async def get_page_asistencias():
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_page_asistencias()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})

@app.get("/api/page/pagos")
async def get_page_pagos():
    api = CepreunaAPI()
    if api.is_logged_in():
        return CepreunaAPI().get_page_pagos()
    return JSONResponse(status_code=403, content={"error": "Sesión expirada o no válida"})