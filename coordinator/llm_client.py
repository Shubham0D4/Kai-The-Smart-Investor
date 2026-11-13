"""
Multi-LLM Client for Kai - Economist
Provides automatic fallback from Gemini to Groq (Llama-3) on quota limits.
"""

import os
import time
import random
from typing import Dict, List, Optional, Any
import google.generativeai as genai
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

# Model ids change: providers retire them (gemini-2.0-flash-exp and llama-3.3-70b-versatile
# both stopped working). Override with KAI_GROQ_MODEL / KAI_GEMINI_MODEL in .env.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class MultiLLMClient:
    """
    Unified LLM client with automatic fallback and retry logic.
    """
    
    def __init__(self, system_instruction: str, model_name: Optional[str] = None):
        """
        Initialize LLM clients.
        
        Args:
            system_instruction: System prompt for the agent
            model_name: Gemini model (default: KAI_GEMINI_MODEL or DEFAULT_GEMINI_MODEL)

        Groq is tried first (KAI_GROQ_MODEL or DEFAULT_GROQ_MODEL), then Gemini.
        """
        self.system_instruction = system_instruction
        self.primary_model_name = model_name or os.getenv("KAI_GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
        self.fallback_model_name = os.getenv("KAI_GROQ_MODEL") or DEFAULT_GROQ_MODEL
        
        # Initialize Gemini
        gemini_key = os.getenv("GOOGLE_API_KEY")
        if gemini_key:
            genai.configure(api_key=gemini_key)
            self.gemini_model = genai.GenerativeModel(
                model_name=self.primary_model_name,
                system_instruction=system_instruction
            )
        else:
            self.gemini_model = None
            
        # Initialize Groq
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key:
            self.groq_client = Groq(api_key=groq_key)
        else:
            self.groq_client = None
            
    def generate_content(self, prompt: str, max_retries: int = 3) -> Any:
        """
        Generate content with priority: Groq -> Gemini.
        """
        groq_error = None
        # 1. Try Groq first
        if self.groq_client:
            try:
                chat_completion = self.groq_client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": self.system_instruction},
                        {"role": "user", "content": prompt}
                    ],
                    model=self.fallback_model_name, # Using what was "fallback" as primary now
                )
                
                # Wrap Groq response to look like Gemini response
                class GroqResponseWrapper:
                    def __init__(self, text, prompt_tokens, completion_tokens):
                        self.text = text
                        class UsageMetadata:
                            def __init__(self, pt, ct):
                                self.prompt_token_count = pt
                                self.candidates_token_count = ct
                                self.total_token_count = pt + ct
                        self.usage_metadata = UsageMetadata(prompt_tokens, completion_tokens)
                
                return GroqResponseWrapper(
                    chat_completion.choices[0].message.content,
                    chat_completion.usage.prompt_tokens,
                    chat_completion.usage.completion_tokens
                )
            except Exception as e:
                groq_error = e
                print(f"[LLM] Groq call failed ({self.fallback_model_name}): {e}. Falling back to Gemini...")
                # Proceed to Gemini fallback
        
        # 2. Fallback to Gemini
        if self.gemini_model:
            for attempt in range(max_retries):
                try:
                    return self.gemini_model.generate_content(prompt)
                except Exception as e:
                    if groq_error is not None and "429" not in str(e):
                        # Both providers failed: say so, with both reasons, not just the last one
                        raise RuntimeError(
                            f"Groq ({self.fallback_model_name}) failed: {groq_error} | "
                            f"Gemini ({self.primary_model_name}) failed: {e}") from e
                    if "429" in str(e):
                        if attempt < max_retries - 1:
                            delay = 5 * (2 ** attempt) + random.uniform(0, 1)
                            print(f"[LLM] Gemini quota exceeded. Retrying in {delay:.2f}s...")
                            time.sleep(delay)
                            continue
                        else:
                            print(f"[LLM] Gemini quota exhausted and Groq failed/unavailable.")
                            raise e 
                    else:
                        raise e
            return # Should be unreachable if max_retries > 0 loop logic holds or raises
        else:
            if groq_error is not None:
                raise RuntimeError(f"Groq ({self.fallback_model_name}) failed: {groq_error}; "
                                   f"no Gemini key configured") from groq_error
            raise ValueError("No LLM clients available: set GROQ_API_KEY and/or GOOGLE_API_KEY.")


class FakeResponse:
    """Mimics the response object returned by MultiLLMClient."""

    def __init__(self, text: str, prompt_tokens: int, completion_tokens: int):
        self.text = text

        class UsageMetadata:
            prompt_token_count = prompt_tokens
            candidates_token_count = completion_tokens
            total_token_count = prompt_tokens + completion_tokens

        self.usage_metadata = UsageMetadata()


class FakeLLMClient:
    """
    Offline stand-in for MultiLLMClient: same constructor and generate_content
    interface, returns canned text and never touches the network.

    ``responses`` may be:
      * None       - return DEFAULT_RESPONSE every time
      * a str      - return that text every time
      * a list     - return items in order, repeating the last one when exhausted
      * a callable - called with the prompt, returns the text

    Every prompt received is recorded in ``self.calls``.
    """

    DEFAULT_RESPONSE = "Canned analysis. Overall stance: Hold."

    def __init__(
        self,
        system_instruction: str = "",
        model_name: str = "fake-llm",
        responses: Any = None,
    ):
        self.system_instruction = system_instruction
        self.primary_model_name = model_name
        self.responses = responses
        self.calls: List[str] = []

    def _next_text(self, prompt: str) -> str:
        r = self.responses
        if r is None:
            return self.DEFAULT_RESPONSE
        if callable(r):
            return r(prompt)
        if isinstance(r, str):
            return r
        idx = min(len(self.calls) - 1, len(r) - 1)
        return r[idx]

    def generate_content(self, prompt: str, max_retries: int = 3) -> FakeResponse:
        self.calls.append(prompt)
        text = self._next_text(prompt)
        return FakeResponse(text, len(prompt.split()), len(text.split()))
